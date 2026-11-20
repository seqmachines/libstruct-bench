import json
import base64
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from libstruct_bench.cli.run_openrouter_library_v0 import (
    OpenRouterResponseError,
    _chat_payload,
    _clear_generated_outputs,
    _direct_file_ref,
    _run_one,
    main,
    _prompt,
    _response_text,
    _write_summary,
)


class RunOpenRouterLibraryV0Tests(unittest.TestCase):
    def run_one(self, root, **kwargs):
        options = dict(
            config={"input_repo": "example/repo"},
            protocol={"protocol_id": "drop_seq", "input_paths": ["drop_seq/paper.pdf"]},
            model="model-a", out_dir=root, api_key="test", chat_url="https://example.invalid",
            files_url="https://example.invalid", file_mode="direct", pdf_engine="",
            max_completion_tokens=64000, temperature=None, reasoning_effort="high",
            dry_run=False, force=False, skip_existing_errors=False,
            request_retries=2, retry_delay_sec=0,
        )
        options.update(kwargs)
        return _run_one(**options)

    def test_successful_retry_preserves_old_attempt_and_clears_error(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "model-a/drop_seq"
            run.mkdir(parents=True)
            (run / "request.json").write_text('{"old_request": true}')
            (run / "error.json").write_text('{"error": "old timeout"}')
            files = {"path": "drop_seq/paper.pdf", "filename": "paper.pdf",
                     "mime_type": "application/pdf", "data": b"original"}
            success = {"choices": [{"message": {"content": '{"libraries": []}'}}]}
            with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input", return_value=files), \
                    patch("libstruct_bench.cli.run_openrouter_library_v0._post_json", side_effect=[
                        OpenRouterResponseError("timeout", status_code=504), success,
                    ]) as post, \
                    patch("libstruct_bench.pdf_transport.optimize_pdf", return_value=(b"optimized", {
                        "source_size_bytes": 8, "size_bytes": 9, "pages_verified": 1,
                    })) as optimize:
                result = self.run_one(root, optimize_pdfs=True, pdf_image_dpi=200,
                                      provider_only=["google-vertex/global"])
                optimize.assert_called_once_with(b"original", image_dpi=200)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(post.call_count, 2)
            self.assertFalse((run / "error.json").exists())
            with zipfile.ZipFile(next((run / "attempt_history").glob("*.zip"))) as archive:
                self.assertEqual(json.loads(archive.read("error.json"))["error"], "old timeout")
                self.assertTrue(json.loads(archive.read("request.json"))["old_request"])
            request = json.loads((run / "request.json").read_text())
            self.assertEqual(request["provider"], {"only": ["google-vertex/global"]})
            self.assertEqual(request["max_tokens"], 64000)
            ref = request["messages"][1]["content"][1]["file"]["file_data"]
            self.assertEqual(base64.b64decode(ref.split(",", 1)[1]), b"optimized")
            self.assertIn("optimization", json.loads((run / "input_files.json").read_text())[0])
            self.assertEqual(len((run / "request_errors.jsonl").read_text().splitlines()), 1)

    def test_permanent_client_errors_are_not_retried_for_http_or_in_body_errors(self):
        for failure in (OpenRouterResponseError("too large", status_code=413),
                        {"error": {"code": "413", "message": "too large"}},
                        OpenRouterResponseError("message size exceeds 30.000MB limit", status_code=400),
                        {"error": {"code": 400, "message": "Provider returned error",
                                   "metadata": {"raw": "The message size exceeds 30.000MB limit."}}},
                        OpenRouterResponseError("unauthorized", status_code=401)):
            with self.subTest(failure=failure), TemporaryDirectory() as tmp:
                root = Path(tmp)
                file = {"path": "drop_seq/paper.pdf", "filename": "paper.pdf",
                        "mime_type": "application/pdf", "data": b"original"}
                with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input", return_value=file), \
                        patch("libstruct_bench.cli.run_openrouter_library_v0._post_json", side_effect=[failure]) as post:
                    with self.assertRaises(OpenRouterResponseError):
                        self.run_one(root)
                self.assertEqual(post.call_count, 1)
                self.assertTrue((root / "model-a/drop_seq/error.json").exists())
                if isinstance(failure, dict) and failure["error"]["code"] == 400:
                    detail = (root / "model-a/drop_seq/failed_response.txt").read_text()
                    self.assertIn("30.000MB", detail)

    def test_retry_does_not_touch_completed_prediction(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "model-a/drop_seq"
            run.mkdir(parents=True)
            (run / "prediction.json").write_text("original result")
            with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input") as download:
                self.assertEqual(self.run_one(root, optimize_pdfs=True)["status"], "skipped")
                download.assert_not_called()
            self.assertEqual((run / "prediction.json").read_text(), "original result")
            self.assertFalse((run / "attempt_history").exists())

    def test_request_size_guard_rejects_before_model_call(self):
        for options in ({"max_request_bytes": 100},
                        {"model": "anthropic/claude-opus-5", "provider_only": ["google-vertex/global"]}):
            with self.subTest(options=options), TemporaryDirectory() as tmp:
                root = Path(tmp)
                data = b"a" * 22_300_000 if "model" in options else b"pdf"
                file = {"path": "drop_seq/paper.pdf", "filename": "paper.pdf",
                        "mime_type": "application/pdf", "data": data}
                with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input", return_value=file), \
                        patch("libstruct_bench.cli.run_openrouter_library_v0._post_json") as post:
                    with self.assertRaisesRegex(OpenRouterResponseError, "No model request sent"):
                        self.run_one(root, **options)
                    post.assert_not_called()

    def test_image_dpi_cli_requires_explicit_optimization_and_positive_value(self):
        for flags in (["--pdf-image-dpi", "200"],
                      ["--optimize-pdfs", "--pdf-image-dpi", "0"]):
            with self.subTest(flags=flags), patch(
                "libstruct_bench.cli.run_openrouter_library_v0._download_input"
            ) as download:
                with self.assertRaises(SystemExit) as exc:
                    main(["--out", "unused", *flags])
                self.assertEqual(exc.exception.code, 2)
                download.assert_not_called()

    def test_transient_client_errors_are_still_retried(self):
        for code in [408, 425, 429]:
            with self.subTest(code=code), TemporaryDirectory() as tmp:
                file = {"path": "drop_seq/paper.pdf", "filename": "paper.pdf",
                        "mime_type": "application/pdf", "data": b"pdf"}
                with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input", return_value=file), \
                        patch("libstruct_bench.cli.run_openrouter_library_v0._post_json", side_effect=[
                            OpenRouterResponseError("transient", status_code=code),
                            {"choices": [{"message": {"content": '{"libraries": []}'}}]},
                        ]) as post:
                    self.assertEqual(self.run_one(Path(tmp))["status"], "completed")
                    self.assertEqual(post.call_count, 2)

    def test_interrupted_retry_wait_keeps_failure_record(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = {"path": "drop_seq/paper.pdf", "filename": "paper.pdf",
                    "mime_type": "application/pdf", "data": b"original"}
            with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input", return_value=file), \
                    patch("libstruct_bench.cli.run_openrouter_library_v0._post_json",
                          side_effect=OpenRouterResponseError("timeout", status_code=504)), \
                    patch("libstruct_bench.cli.run_openrouter_library_v0.time.sleep", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_one(root)
            run = root / "model-a/drop_seq"
            self.assertEqual(json.loads((run / "error.json").read_text())["error"], "timeout")
            self.assertEqual(json.loads((run / "request_errors.jsonl").read_text())["attempt"], 1)

    def test_partial_retry_summary_retains_other_models_and_protocols(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_summary(root, [
                {"model": "a", "protocol_id": "one", "status": "completed"},
                {"model": "b", "protocol_id": "two", "status": "failed"},
            ])
            _write_summary(root, [{"model": "b", "protocol_id": "two", "status": "completed"}])
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(len(summary), 2)
            self.assertTrue(all(x["status"] == "completed" for x in summary))

    def test_direct_file_ref_uses_data_url(self):
        ref = _direct_file_ref(
            {
                "filename": "paper.pdf",
                "mime_type": "application/pdf",
                "data": b"abc",
            }
        )

        self.assertEqual(ref["type"], "file")
        self.assertEqual(ref["file"]["filename"], "paper.pdf")
        self.assertEqual(ref["file"]["file_data"], "data:application/pdf;base64,YWJj")

    def test_chat_payload_uses_file_parser_plugin_for_direct_files(self):
        payload = _chat_payload(
            model="openai/gpt-5.5",
            prompt="Return JSON.",
            file_refs=[
                {
                    "type": "file",
                    "file": {
                        "filename": "paper.pdf",
                        "file_data": "data:application/pdf;base64,YWJj",
                    },
                }
            ],
            pdf_engine="cloudflare-ai",
            max_completion_tokens=128,
            temperature=None,
            reasoning_effort=None,
        )

        content = payload["messages"][1]["content"]
        self.assertEqual(payload["max_tokens"], 128)
        self.assertEqual(payload["plugins"], [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}])
        self.assertEqual(content[1]["type"], "file")
        self.assertIn("file_data", content[1]["file"])

    def test_chat_payload_omits_file_parser_plugin_by_default(self):
        payload = _chat_payload(
            model="openai/gpt-5.5",
            prompt="Return JSON.",
            file_refs=[
                {
                    "type": "file",
                    "file": {
                        "filename": "paper.pdf",
                        "file_data": "data:application/pdf;base64,YWJj",
                    },
                }
            ],
            pdf_engine="",
            max_completion_tokens=128,
            temperature=None,
            reasoning_effort=None,
        )

        self.assertNotIn("plugins", payload)

    def test_chat_payload_supports_reasoning_effort(self):
        payload = _chat_payload(
            model="openai/gpt-5.6-sol",
            prompt="Return JSON.",
            file_refs=[],
            pdf_engine="",
            max_completion_tokens=128,
            temperature=None,
            reasoning_effort="max",
        )

        self.assertEqual(payload["reasoning"], {"effort": "max", "exclude": True})

    def test_prompt_prohibits_external_search(self):
        prompt = _prompt(
            protocol_id="example_protocol",
            display_name="Example",
            input_paths=["example/protocol.pdf"],
        )

        self.assertIn("STRICTLY PROHIBITED: do not search the web", prompt)
        self.assertIn("scg_lib_struct", prompt)
        self.assertIn("Use only the attached protocol input", prompt)
        self.assertIn("must be a non-empty string", prompt)
        self.assertIn("writing an empty string", prompt)
        self.assertIn("anchored oligo-dT suffixes", prompt)
        self.assertIn("not become `??`", prompt)
        self.assertIn("dummy schema", prompt)
        self.assertIn("not source evidence", prompt)
        self.assertIn('"library_sequence": "ACGT####~~~~TGCA"', prompt)
        self.assertNotIn("AATGATACGGCGACCACCGAGATCTACAC################", prompt)
        self.assertIn("How to derive the structure", prompt)
        self.assertIn("simulating library construction forward", prompt)
        self.assertIn("reverse-complement", prompt)
        self.assertIn("Reconcile against the sequencing reads", prompt)
        self.assertIn("self-check each entry", prompt)

    def test_response_text_explains_reasoning_only_completion(self):
        with self.assertRaisesRegex(OpenRouterResponseError, "reasoning_tokens=128"):
            _response_text(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": None},
                        }
                    ],
                    "usage": {
                        "completion_tokens": 128,
                        "completion_tokens_details": {"reasoning_tokens": 128},
                    },
                }
            )

    def test_clear_generated_outputs_removes_stale_prediction(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for filename in ["prediction.json", "raw_response.txt", "response.json", "error.json"]:
                (tmp_path / filename).write_text("stale", encoding="utf-8")
            (tmp_path / "request.json").write_text("keep", encoding="utf-8")

            _clear_generated_outputs(tmp_path)

            self.assertFalse((tmp_path / "prediction.json").exists())
            self.assertTrue((tmp_path / "request.json").exists())

    def test_run_one_skips_existing_error_by_default(self):
        with TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model-a" / "drop_seq"
            model_dir.mkdir(parents=True)
            (model_dir / "error.json").write_text(
                json.dumps({"error": "known provider failure"}),
                encoding="utf-8",
            )

            result = _run_one(
                config={"input_repo": "example/repo"},
                protocol={"protocol_id": "drop_seq", "input_paths": ["drop_seq/protocol.pdf"]},
                model="model-a",
                out_dir=Path(tmp),
                api_key="test-key",
                chat_url="https://example.invalid/chat",
                files_url="https://example.invalid/files",
                file_mode="direct",
                pdf_engine="",
                max_completion_tokens=128,
                temperature=None,
                reasoning_effort=None,
                dry_run=False,
                force=False,
                skip_existing_errors=True,
                request_retries=0,
                retry_delay_sec=0,
            )

            self.assertEqual(result["status"], "skipped_error")
            self.assertEqual(result["error"], "known provider failure")

    def test_main_can_continue_after_openrouter_error(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            protocols_path = tmp_path / "protocols.json"
            out_dir = tmp_path / "out"
            protocols_path.write_text(
                json.dumps(
                    {
                        "input_repo": "example/repo",
                        "input_revision": "main",
                        "protocols": [
                            {
                                "protocol_id": "drop_seq",
                                "display_name": "Drop-seq",
                                "input_paths": ["drop_seq/protocol.pdf"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "libstruct_bench.cli.run_openrouter_library_v0._run_one",
                side_effect=[
                    OpenRouterResponseError("temporary 500"),
                    {"model": "model-b", "protocol_id": "drop_seq", "status": "completed"},
                ],
            ):
                exit_code = main(
                    [
                        "--protocols",
                        str(protocols_path),
                        "--out",
                        str(out_dir),
                        "--model",
                        "model-a",
                        "--model",
                        "model-b",
                        "--dry-run",
                        "--continue-on-error",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary[0]["status"], "failed")
            self.assertEqual(summary[0]["error"], "temporary 500")
            self.assertEqual(summary[1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()

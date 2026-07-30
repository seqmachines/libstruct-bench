import json
import unittest
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
)


class RunOpenRouterLibraryV0Tests(unittest.TestCase):
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

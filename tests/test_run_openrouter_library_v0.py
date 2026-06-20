import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from libstruct_bench.cli.run_openrouter_library_v0 import (
    OpenRouterResponseError,
    _chat_payload,
    _clear_generated_outputs,
    _direct_file_ref,
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
            model="openai/gpt-5.5",
            prompt="Return JSON.",
            file_refs=[],
            pdf_engine="",
            max_completion_tokens=128,
            temperature=None,
            reasoning_effort="minimal",
        )

        self.assertEqual(payload["reasoning"], {"effort": "minimal", "exclude": True})

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


if __name__ == "__main__":
    unittest.main()

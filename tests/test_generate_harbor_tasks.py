import json
import tempfile
import unittest
from pathlib import Path

from libstruct_bench.cli.generate_harbor_tasks import main


class GenerateHarborTasksTests(unittest.TestCase):
    def test_generates_multi_file_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            protocols_path = tmp_path / "protocols.json"
            out_dir = tmp_path / "tasks"
            protocols_path.write_text(
                json.dumps(
                    {
                        "schema_version": "libstruct.oligo_extraction.protocols.v1",
                        "input_repo": "seqmachines/scg-protocols-v1",
                        "groundtruth_repo": "seqmachines/scg-oligo-groundtruth-v1",
                        "input_revision": "abc123",
                        "groundtruth_revision": "def456",
                        "protocols": [
                            {
                                "protocol_id": "example_protocol",
                                "display_name": "Example Protocol",
                                "input_kind": "raw protocol",
                                "input_paths": [
                                    "example_protocol/paper.pdf",
                                    "example_protocol/supp.xlsx",
                                ],
                                "groundtruth_path": "example_protocol/groundtruth_oligos.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                main(["--protocols", str(protocols_path), "--out", str(out_dir)]),
                0,
            )

            task_dir = out_dir / "example_protocol"
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            fetch_input = (task_dir / "environment" / "fetch_input.py").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
            test_sh = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")

            self.assertIn("input/paper.pdf", instruction)
            self.assertIn("input/supp.xlsx", instruction)
            self.assertIn("REPO_ID = 'seqmachines/scg-protocols-v1'", fetch_input)
            self.assertIn("example_protocol/paper.pdf", fetch_input)
            self.assertIn("example_protocol/supp.xlsx", fetch_input)
            self.assertIn('input_revision = "abc123"', task_toml)
            self.assertIn('groundtruth_revision = "def456"', task_toml)
            self.assertIn('network_mode = "public"', task_toml)
            self.assertNotIn("allowed_hosts", task_toml)
            self.assertNotIn("*.huggingface.co", task_toml)
            self.assertIn('--groundtruth-repo "seqmachines/scg-oligo-groundtruth-v1"', test_sh)
            self.assertNotIn("LIBSTRUCT_HF_INPUT_REPO", task_toml)
            self.assertNotIn("LIBSTRUCT_HF_GROUNDTRUTH_REPO", task_toml)
            self.assertTrue((task_dir / "tests" / "libstruct_bench" / "grader.py").exists())

    def test_supports_legacy_input_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            protocols_path = tmp_path / "protocols.json"
            out_dir = tmp_path / "tasks"
            protocols_path.write_text(
                json.dumps(
                    {
                        "schema_version": "libstruct.oligo_extraction.protocols.v1",
                        "input_repo": "seqmachines/scg-protocols-v1",
                        "groundtruth_repo": "seqmachines/scg-oligo-groundtruth-v1",
                        "input_revision": "main",
                        "groundtruth_revision": "main",
                        "protocols": [
                            {
                                "protocol_id": "legacy_protocol",
                                "input_path": "legacy_protocol/protocol.pdf",
                                "groundtruth_path": "legacy_protocol/groundtruth_oligos.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                main(["--protocols", str(protocols_path), "--out", str(out_dir)]),
                0,
            )

            fetch_input = (
                out_dir / "legacy_protocol" / "environment" / "fetch_input.py"
            ).read_text(encoding="utf-8")
            self.assertIn("INPUT_PATHS = ['legacy_protocol/protocol.pdf']", fetch_input)


if __name__ == "__main__":
    unittest.main()

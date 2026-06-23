import json
import tempfile
import unittest
from pathlib import Path

from libstruct_bench.cli.generate_library_v0_tasks import main


class GenerateLibraryV0TasksTests(unittest.TestCase):
    def test_generates_multi_file_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            protocols_path = tmp_path / "protocols.json"
            out_dir = tmp_path / "tasks"
            protocols_path.write_text(
                json.dumps(
                    {
                        "schema_version": "libstruct.library_structure.protocols.v0",
                        "input_repo": "sequencing/scg-protocols-v1",
                        "groundtruth_repo": "sequencing/scg-oligo-groundtruth-v1",
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
                                "groundtruth_path": "example_protocol/groundtruth_final_lib_struct.json",
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
            environment_dockerfile = (task_dir / "environment" / "Dockerfile").read_text(
                encoding="utf-8"
            )
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
            test_sh = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")

            self.assertIn("input/paper.pdf", instruction)
            self.assertIn("input/supp.xlsx", instruction)
            self.assertIn('"libraries"', instruction)
            self.assertIn('"library_sequence"', instruction)
            self.assertIn("verifier reads only that file", instruction)
            self.assertIn("JSON returned only in your final chat response is", instruction)
            self.assertIn("test -s /logs/artifacts/prediction.json", instruction)
            self.assertIn("python -m json.tool /logs/artifacts/prediction.json", instruction)
            self.assertIn("must be a non-empty string", instruction)
            self.assertIn("rather than", instruction)
            self.assertIn("writing an empty string", instruction)
            self.assertIn("anchored oligo-dT suffixes", instruction)
            self.assertIn("not become `??`", instruction)
            self.assertIn("STRICTLY PROHIBITED: do not search the web", instruction)
            self.assertIn("scg_lib_struct", instruction)
            self.assertIn("Do not answer from protocol name", instruction)
            self.assertIn("use PyMuPDF", instruction)
            self.assertIn("use `openpyxl` for `.xlsx` files", instruction)
            self.assertIn("Do not", instruction)
            self.assertIn("pypdf", instruction)
            self.assertIn("separate `rna` and `atac` entries", instruction)
            self.assertIn("REPO_ID = 'sequencing/scg-protocols-v1'", fetch_input)
            self.assertIn("example_protocol/paper.pdf", fetch_input)
            self.assertIn("pymupdf", environment_dockerfile)
            self.assertIn("openpyxl", environment_dockerfile)
            self.assertIn("pillow", environment_dockerfile)
            self.assertIn("apt-get install -y --no-install-recommends file", environment_dockerfile)
            self.assertIn("COPY fetch_input.py /workspace/fetch_input.py", environment_dockerfile)
            self.assertNotIn("pypdf", environment_dockerfile.lower())
            self.assertIn('benchmark = "library_structure_v0"', task_toml)
            self.assertIn('input_revision = "abc123"', task_toml)
            self.assertIn('groundtruth_revision = "def456"', task_toml)
            self.assertIn("[agent.env]", task_toml)
            self.assertIn('CODEX_FORCE_AUTH_JSON = "1"', task_toml)
            self.assertNotIn("docker_image", task_toml)
            self.assertIn('--groundtruth-repo "sequencing/scg-oligo-groundtruth-v1"', test_sh)
            self.assertIn('--groundtruth-path "example_protocol/groundtruth_final_lib_struct.json"', test_sh)
            self.assertIn("--audit-out /logs/verifier/audit.json", test_sh)
            self.assertTrue((task_dir / "tests" / "libstruct_bench" / "library_structure.py").exists())

    def test_supports_legacy_input_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            protocols_path = tmp_path / "protocols.json"
            out_dir = tmp_path / "tasks"
            protocols_path.write_text(
                json.dumps(
                    {
                        "schema_version": "libstruct.library_structure.protocols.v0",
                        "input_repo": "sequencing/scg-protocols-v1",
                        "groundtruth_repo": "sequencing/scg-oligo-groundtruth-v1",
                        "input_revision": "main",
                        "groundtruth_revision": "main",
                        "protocols": [
                            {
                                "protocol_id": "legacy_protocol",
                                "input_path": "legacy_protocol/protocol.pdf",
                                "groundtruth_path": "legacy_protocol/groundtruth_final_lib_struct.json",
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

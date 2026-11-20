import ast
import contextlib
import copy
import hashlib
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from libstruct_bench.cli.generate_library_v0_tasks import main as generate_tasks
from libstruct_bench.cli.run_openrouter_library_v0 import main as run_api
from libstruct_bench.library_structure_inputs import PDF_ONLY_INSTRUCTION


ROOT = Path(__file__).resolve().parents[1]
ROUND2 = ROOT / "paper/benchmarks/task1"


class LibraryStructureRound2Tests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROUND2 / "protocols.json").read_text())

    def test_round2_removes_only_xlsx_and_preserves_protocols_and_revision(self):
        original = json.loads((ROOT / "paper/benchmarks/task1/provenance/original_protocols.json").read_text())
        self.assertEqual(self.manifest["input_revision"], original["input_revision"])
        self.assertEqual(len(self.manifest["protocols"]), 20)
        self.assertEqual(
            [p["protocol_id"] for p in self.manifest["protocols"]],
            [p["protocol_id"] for p in original["protocols"]],
        )
        exclusions = {}
        for before, after in zip(original["protocols"], self.manifest["protocols"]):
            removed = [p for p in before["input_paths"] if Path(p).suffix.lower() == ".xlsx"]
            expected = copy.deepcopy(before)
            expected["input_paths"] = [p for p in before["input_paths"] if p not in removed]
            self.assertEqual(after, expected)
            self.assertTrue(after["input_paths"])
            if removed:
                exclusions[before["protocol_id"]] = removed
        self.assertEqual(exclusions, self.manifest["excluded_input_paths"])
        self.assertEqual(sum(map(len, exclusions.values())), 7)
        self.assertEqual(len(exclusions), 6)

    def test_all_api_attachments_match_harbor_inputs_and_frozen_labels_stay_private(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            labels = root / "labels"
            labels.mkdir()
            for p in self.manifest["protocols"]:
                (labels / f"{p['protocol_id']}.json").write_text(json.dumps({
                    "schema_version": "libstruct.library_structure.v0",
                    "protocol_id": p["protocol_id"],
                    "libraries": [{"library_id": "rna", "modality": "RNA", "library_sequence": "ACGT"}],
                }))
            # Vendoring is covered by the existing generator tests; here exercise
            # every protocol's generated fetcher, instructions and verifier wiring.
            with patch("libstruct_bench.cli.generate_library_v0_tasks.shutil.copytree"):
                generate_tasks([
                    "--protocols", str(ROUND2 / "protocols.json"),
                    "--out", str(root / "tasks"), "--groundtruth-dir", str(labels),
                ])

            downloaded = []

            def download(**kwargs):
                downloaded.append(kwargs["input_path"])
                return {"path": kwargs["input_path"], "filename": Path(kwargs["input_path"]).name,
                        "mime_type": "application/pdf", "data": b"%PDF-test"}

            with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input", side_effect=download), \
                    patch("libstruct_bench.cli.run_openrouter_library_v0._post_json") as post:
                run_api(["--protocols", str(ROUND2 / "protocols.json"),
                         "--out", str(root / "api"), "--dry-run"])
                post.assert_not_called()

            self.assertEqual(len(downloaded), 90)  # 30 PDFs x 3 models
            self.assertTrue(all(Path(p).suffix == ".pdf" for p in downloaded))
            requests = list((root / "api").glob("*/*/request.json"))
            self.assertEqual(len(requests), 60)
            for p in self.manifest["protocols"]:
                task = root / "tasks" / p["protocol_id"]
                fetch = ast.parse((task / "environment/fetch_input.py").read_text())
                assignments = {n.targets[0].id: ast.literal_eval(n.value) for n in fetch.body
                               if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
                self.assertEqual(assignments["INPUT_PATHS"], p["input_paths"])
                self.assertEqual(assignments["REVISION"], self.manifest["input_revision"])
                instruction = (task / "instruction.md").read_text()
                self.assertIn(PDF_ONLY_INSTRUCTION, instruction)
                self.assertNotIn("For spreadsheet parsing", instruction)
                frozen = (labels / f"{p['protocol_id']}.json").read_bytes()
                self.assertEqual((task / "tests/groundtruth.json").read_bytes(), frozen)
                self.assertIn(hashlib.sha256(frozen).hexdigest(), (task / "task.toml").read_text())
                self.assertNotIn("HF_TOKEN", (task / "task.toml").read_text())
                self.assertNotIn("[agent.env]", (task / "task.toml").read_text())
                self.assertFalse((task / "environment/groundtruth.json").exists())
                verifier = (task / "tests/test.sh").read_text()
                self.assertIn("--groundtruth /tests/groundtruth.json", verifier)
                self.assertNotIn("--groundtruth-repo", verifier)
                for request_path in (root / "api").glob(f"*/{p['protocol_id']}/request.json"):
                    request = json.loads(request_path.read_text())
                    self.assertEqual(request["max_tokens"], 64000)
                    self.assertEqual(request["reasoning"]["effort"], "high")
                    self.assertNotIn("plugins", request)
                    content = request["messages"][1]["content"]
                    self.assertIn(PDF_ONLY_INSTRUCTION, content[0]["text"])
                    self.assertEqual([x["file"]["filename"] for x in content[1:]],
                                     [Path(x).name for x in assignments["INPUT_PATHS"]])

    def test_pdf_only_policy_rejected_before_download_or_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.manifest["protocols"][0]["input_paths"].append("unexpected.XLSX")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest))
            with patch("libstruct_bench.cli.run_openrouter_library_v0._download_input") as download:
                for main in (run_api, generate_tasks):
                    with self.assertRaisesRegex(ValueError, "pdf_only"):
                        main(["--protocols", str(manifest_path), "--out", str(root / "output")])
                download.assert_not_called()
            self.assertFalse((root / "output").exists())

    def test_api_overrides_remain_explicit_and_cli_models_use_high(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            with patch("libstruct_bench.cli.run_openrouter_library_v0._run_one", return_value={
                "model": "example/model", "protocol_id": "drop_seq", "status": "dry_run",
            }) as run:
                run_api(["--protocols", str(ROUND2 / "protocols.json"), "--out", tmp,
                         "--dry-run", "--protocol-id", "drop_seq", "--model", "example/model",
                         "--reasoning-effort", "medium", "--max-completion-tokens", "12345"])
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.kwargs["model"], "example/model")
                self.assertEqual(run.call_args.kwargs["reasoning_effort"], "medium")
                self.assertEqual(run.call_args.kwargs["max_completion_tokens"], 12345)
        expected = {"codex": "gpt-5.6-sol", "claude-code": "claude-opus-5",
                    "antigravity-cli": "google/gemini-3.7-flash-high"}
        for harness, model in expected.items():
            config = json.loads((ROOT / "paper/benchmarks/task1" / f"{harness}.json").read_text())
            self.assertEqual(config["n_attempts"], 1)
            self.assertEqual(config["datasets"][0]["path"], "runs/library_structure/round2/tasks")
            self.assertEqual(config["agents"][0]["model_name"], model)
            self.assertEqual(config["agents"][0]["kwargs"]["reasoning_effort"], "high")
            self.assertFalse({"CODEX_FORCE_AUTH_JSON", "CLAUDE_FORCE_OAUTH", "AGY_FORCE_AUTH_JSON"}
                             & config["agents"][0].get("env", {}).keys())

    def test_host_auth_switches_preserve_results_and_real_secrets_still_scrub(self):
        # Exercise the installed Harbor behavior that corrupted round 2, without
        # starting containers, reading real credentials, or making model calls.
        try:
            from harbor.agents.installed.antigravity_cli import AntigravityCli
            from harbor.agents.installed.claude_code import ClaudeCode
            from harbor.agents.installed.codex import Codex
            from harbor.trial.trial import Trial
            from harbor.utils.env import resolve_env_vars
        except ModuleNotFoundError as exc:
            if exc.name == "harbor":
                self.skipTest("Harbor is an optional local integration dependency")
            raise

        cases = [
            ("codex", Codex, "CODEX_FORCE_AUTH_JSON", ".codex/auth.json"),
            ("claude-code", ClaudeCode, "CLAUDE_FORCE_OAUTH", None),
            ("antigravity-cli", AntigravityCli, "AGY_FORCE_AUTH_JSON",
             ".gemini/antigravity-cli/antigravity-oauth-token"),
        ]
        for harness, adapter_class, switch, credential_path in cases:
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake_token = "test-only-oauth-secret-ABCDEFGHIJK"
                host_env = {switch: "1", "CLAUDE_CODE_OAUTH_TOKEN": fake_token}
                config = json.loads((ROOT / "paper/benchmarks/task1" / f"{harness}.json").read_text())
                with patch.dict(os.environ, host_env, clear=True), \
                        patch.object(Path, "home", return_value=root):
                    adapter = object.__new__(adapter_class)
                    adapter._extra_env = resolve_env_vars(config["agents"][0].get("env", {}))
                    if credential_path:
                        credential = root / credential_path
                        credential.parent.mkdir(parents=True)
                        credential.write_text("test-only-credential")
                        resolve = (adapter._resolve_auth_json_path if harness == "codex"
                                   else adapter._resolve_auth_token_path)
                        self.assertEqual(resolve(), credential)
                    else:
                        self.assertTrue(adapter._should_force_oauth())
                        self.assertEqual(adapter._get_env("CLAUDE_CODE_OAUTH_TOKEN"), fake_token)

                    trial_dir = root / "trial"
                    trial_dir.mkdir()
                    output = trial_dir / "result.json"
                    original = json.dumps({"reward": 1.0, "library_f1": 1.0,
                                           "protocol_id": "10x_example",
                                           "annotation": "[CELL_BARCODE:16]", "sequence": "ACGT??"})
                    output.write_text(original)
                    if harness == "claude-code":
                        (trial_dir / "agent.log").write_text(f"token={fake_token}")
                    trial = SimpleNamespace(
                        agent=adapter,
                        task=SimpleNamespace(config=SimpleNamespace(verifier=SimpleNamespace(env={}))),
                        config=SimpleNamespace(verifier=SimpleNamespace(env={})),
                        paths=SimpleNamespace(trial_dir=trial_dir),
                        logger=logging.getLogger(__name__),
                    )
                    Trial._scrub_jobs_dir(trial)
                    self.assertEqual(output.read_text(), original)
                    if harness == "claude-code":
                        self.assertEqual((trial_dir / "agent.log").read_text(), "token=[REDACTED]")


if __name__ == "__main__":
    unittest.main()

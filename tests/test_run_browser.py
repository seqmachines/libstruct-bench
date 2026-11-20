import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def browser():
    path = Path(__file__).resolve().parents[1] / "tools/build_run_browser.py"
    spec = importlib.util.spec_from_file_location("run_browser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setting_preserves_scientific_limits_but_not_credentials(browser):
    agent = {"kwargs": {"max_output_tokens": 64000}, "env": {"CLAUDE_CODE_OAUTH_TOKEN": "sensitive-value"}}
    config = {"agent": agent}
    a = browser.setting(config, {})
    assert a["agent"]["kwargs"]["max_output_tokens"] == 64000
    assert "sensitive-value" not in json.dumps(a)
    agent["kwargs"]["max_output_tokens"] = 128000
    assert browser.digest(a) != browser.digest(browser.setting(config, {}))


def test_group_identity_separates_conditions_and_learning_checkpoints(browser, tmp_path):
    root = tmp_path / "runs"
    base = root / "libgen/codex/baseline/protocol__abc"
    repeat = root / "libgen/codex/baseline-repeats/protocol__def"
    name = root / "libgen/codex/baseline-name-only/protocol__ghi"
    assert browser.category(base, root, base.parent) == browser.category(repeat, root, repeat.parent)
    assert browser.category(base, root, base.parent) != browser.category(name, root, name.parent)
    a = root / "libgen/agent-guided/evaluation/c30/protocol__abc"
    b = root / "libgen/agent-guided/evaluation/c58/protocol__def"
    assert browser.category(a, root, a.parent) != browser.category(b, root, b.parent)


def test_browser_links_preserve_originals_and_refuse_overwrite(browser, tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "ROOT", tmp_path)
    runs = tmp_path / "runs"
    for jobname, ident in [("baseline", "abc"), ("baseline-repeats", "def")]:
        job = runs / "libgen/codex" / jobname
        trial = job / ("protocol__" + ident)
        (trial / "agent").mkdir(parents=True)
        (job / "config.json").write_text(json.dumps({"agents": [{"name": "codex", "model_name": "model"}]}))
        (trial / "config.json").write_text(json.dumps({"task": {"path": "tasks/protocol"}, "agent": {"name": "codex", "model_name": "model", "kwargs": {"version": "1"}}}))
        (trial / "result.json").write_text(json.dumps({"id": ident, "finished_at": "2026-01-01"}))
        (trial / "agent/trajectory.json").write_text('{"private_trace": "unchanged"}')
    before = {p: p.read_bytes() for p in browser.walk_files(runs)}
    monkeypatch.setattr("sys.argv", ["build_run_browser.py"])
    browser.main()
    out = runs / "by_setting"
    data = json.loads((out / "inventory.json").read_text())
    assert data["summary"]["directory_records"] == 2
    assert data["summary"]["recorded_setting_groups"] == 1
    for row in data["records"]:
        link = out / row["link"]
        assert link.is_symlink()
        assert link.resolve() == tmp_path / row["source"]
    assert "private_trace" not in (out / "index.html").read_text()
    assert before == {p: p.read_bytes() for p in browser.walk_files(runs)}
    with pytest.raises(FileExistsError):
        browser.main()


def test_snapshot_does_not_follow_run_links(browser, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "result.json").write_text("{}")
    (runs / "loop").symlink_to(runs, target_is_directory=True)
    assert list(browser.snapshot(runs)) == ["result.json"]

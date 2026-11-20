import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("harbor")
from harbor.job import Job
from harbor.metrics.mean import Mean
from harbor.models.job.config import JobConfig
from harbor.models.job.result import JobStats
from libstruct_bench.cli.resume_libgen_job import (
    _install_explicit_task_metrics_workaround, _telemetry_root, main,
    _kimi_provider_error,
)


def test_named_explicit_tasks_get_metrics_before_progress_update(monkeypatch):
    monkeypatch.setattr(Job, "_resolve_metrics", Job.__dict__["_resolve_metrics"])
    config = JobConfig.model_validate({"tasks": [
        {"path": "tasks/ddseq", "source": "tasks"},
        {"path": "tasks/ddseq", "source": "tasks"},
        {"path": "tasks/scdamid", "source": "tasks"},
    ]})
    _install_explicit_task_metrics_workaround()
    patched = Job._resolve_metrics
    _install_explicit_task_metrics_workaround()
    assert Job._resolve_metrics is patched
    metrics = asyncio.run(Job._resolve_metrics(config, config.tasks))
    assert len(metrics["tasks"]) == 1
    assert isinstance(metrics["tasks"][0], Mean)
    assert len(config.tasks) == 3  # Repeated ddSEQ attempts remain distinct.
    job = object.__new__(Job)
    job._metrics = metrics
    key = JobStats.format_agent_evals_key("kimi-cli", "moonshotai/kimi-k3", "tasks")
    job._live_rewards = {key: {"scdamid__saved": {"reward": 0.739}}}
    event = SimpleNamespace(config=SimpleNamespace(task=config.tasks[2]),
        result=SimpleNamespace(agent_info=SimpleNamespace(name="kimi-cli",
            model_info=SimpleNamespace(name="moonshotai/kimi-k3"))))
    progress = Mock()
    job._update_metric_display(event, progress, 0)
    progress.update.assert_called_once_with(0, description="Mean: 0.739")


def test_workaround_preserves_existing_metric_instances(monkeypatch):
    marker = object()
    async def resolver(config, task_configs):
        return {"tasks": [marker]}
    monkeypatch.setattr(Job, "_resolve_metrics", staticmethod(resolver))
    _install_explicit_task_metrics_workaround()
    config = SimpleNamespace(metrics=[])
    result = asyncio.run(Job._resolve_metrics(config, [SimpleNamespace(source="tasks")]))
    assert result["tasks"] == [marker]


def test_resume_preserves_incomplete_output_and_uses_process_local_fix(tmp_path, monkeypatch):
    job = tmp_path / "job"
    failed = job / "ddseq__cancelled"
    failed.mkdir(parents=True)
    config_bytes = b'{"tasks":[{"path":"tasks/ddseq","source":"tasks"}]}'
    (job / "config.json").write_bytes(config_bytes)
    (failed / "result.json").write_text(json.dumps({
        "exception_info": {"exception_type": "CancelledError"}, "trial_name": failed.name}))
    pending = job / "pip__interrupted"
    (pending / "artifacts").mkdir(parents=True)
    (pending / "config.json").write_text('{}')
    (pending / "artifacts/t2_prediction.json").write_text('{"partial":true}')
    success = job / "scdamid__done"
    success.mkdir()
    (success / "result.json").write_text('{"exception_info":null}')

    def fake_resume(command, check):
        assert command[:4] == [sys.executable, '-m', 'libstruct_bench.cli.resume_libgen_job', '--harbor-cli']
        assert command[4:] == ['job', 'resume', '-p', str(job), '-f', 'CancelledError']
        shutil.rmtree(pending)
        shutil.rmtree(failed)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr('libstruct_bench.cli.resume_libgen_job.subprocess.run', fake_resume)
    assert main(['-p', str(job), '-f', 'CancelledError']) == 0
    event_path = next(_telemetry_root(job).glob('resume_snapshots/*/resume_event.json'))
    event = json.loads(event_path.read_text())
    assert event['harbor_explicit_task_metrics_workaround'] is True
    assert event['incomplete_trial_snapshots'] == [pending.name]
    assert (event_path.parent/'incomplete'/pending.name/'artifacts/t2_prediction.json').read_text() == '{"partial":true}'
    assert (success/'result.json').read_text() == '{"exception_info":null}'
    assert (job/'config.json').read_bytes() == config_bytes


@pytest.mark.parametrize('encoding', ['json', 'python'])
def test_temporary_kimi_budget_retry_preserves_original_and_timeout(tmp_path, monkeypatch, encoding):
    job = tmp_path / 'job'
    failed = job / 'pip__budget'
    (failed / 'agent').mkdir(parents=True)
    (job / 'config.json').write_text('{}')
    original = {'trial_name': failed.name, 'exception_info': None,
                'agent_info': {'name': 'kimi-cli'}}
    original_bytes = json.dumps(original).encode()
    (failed / 'result.json').write_bytes(original_bytes)
    body = {'error': {'code': 402, 'metadata': {
        'reason': 'in_flight_budget_exhausted', 'limit_source': 'openrouter_in_flight_budget'}}}
    message = 'Error code: 402 - ' + (json.dumps(body) if encoding == 'json' else repr(body))
    (failed / 'agent/kimi-cli.txt').write_text(json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32003, 'message': message}}))
    timeout = job / 'ddseq__timeout'
    timeout.mkdir()
    timeout_bytes = b'{"exception_info":{"exception_type":"AgentTimeoutError"}}'
    (timeout / 'result.json').write_bytes(timeout_bytes)

    def fake_resume(command, check):
        assert command[-2:] == ['-f', 'KimiProviderError']
        shutil.rmtree(failed)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr('libstruct_bench.cli.resume_libgen_job.subprocess.run', fake_resume)
    assert main(['-p', str(job), '--include-kimi-provider-errors']) == 0
    event_path = next(_telemetry_root(job).glob('resume_snapshots/*/resume_event.json'))
    event = json.loads(event_path.read_text())
    assert event['snapshot_count'] == 1
    assert event['rerun_classification'] == 'diagnostic_only'
    assert not event['primary_rerun_eligible']
    assert (event_path.parent / failed.name / 'result.json').read_bytes() == original_bytes
    assert (timeout / 'result.json').read_bytes() == timeout_bytes


@pytest.mark.parametrize('body', [
    {'error': {'code': 402, 'message': 'Insufficient credits'}},
    {'error': {'code': 402, 'metadata': {'reason': 'in_flight_budget_exhausted'}}},
    {'error': {'code': 403, 'metadata': {'reason': 'in_flight_budget_exhausted',
                                      'limit_source': 'openrouter_in_flight_budget'}}},
    {'error': None}, [], 'malformed body',
])
def test_budget_retry_does_not_select_other_credit_or_account_errors(tmp_path, body):
    (tmp_path / 'agent').mkdir()
    (tmp_path / 'agent/kimi-cli.txt').write_text(json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32003,
        'message': 'Error code: 402 - ' + (body if isinstance(body, str) else repr(body))}}))
    assert _kimi_provider_error(tmp_path, {'agent_info': {'name': 'kimi-cli'}}) is None

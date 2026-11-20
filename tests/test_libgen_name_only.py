import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

pytest.importorskip('harbor')

from libstruct_bench.cli.prepare_libgen_name_only import adapt_rules, PROVIDER_HOSTS
from libstruct_bench.cli.analyze_libgen_name_only import metric_eligible, summarize_pairs, score_trial
from libstruct_bench.harbor_agents.name_only import (
    CodexNameOnly, ClaudeNameOnly, AntigravityNameOnly, NameOnlyMixin, AGY_PROFILE, validate_lock,
)
from harbor.agents.installed.codex import Codex


def test_rules_change_only_evidence_grounding():
    rules = (Path(__file__).parents[1] / 'paper/benchmarks/tasks2_3/rules.md').read_text()
    adapted = adapt_rules(rules)
    assert 'internal knowledge only' in adapted
    assert '/workspace/input/' not in adapted
    assert 'Before returning the graph, fact-check' not in adapted
    assert adapted.split('## Output and validation')[1] == rules.split('## Output and validation')[1]
    assert 'same local T2 oligo IDs from T3' in adapted
    with pytest.raises(ValueError, match='Baseline rules changed'):
        adapt_rules(rules.replace('Use only the files preloaded', 'Read the files preloaded'))


def make_agent(tmp_path, cls, **overrides):
    lock = tmp_path / 'experiment.json'
    lock.write_text('{"generated_files":{}}')
    kwargs = dict(experiment_lock=str(lock), model_name=cls.expected_model,
                  version=cls.expected_version, reasoning_effort=cls.expected_effort)
    if cls is AntigravityNameOnly:
        adc = tmp_path / 'adc.json'
        adc.write_text('{"quota_project_id":"test-project"}')
        kwargs.update(gcp_project='test-project', adc_path=str(adc))
    kwargs.update(overrides)
    return cls(tmp_path / 'logs', **kwargs)


@pytest.mark.parametrize('cls', [CodexNameOnly, ClaudeNameOnly, AntigravityNameOnly])
def test_native_adapters_reject_drift_and_history(tmp_path, cls):
    for extra in ({'version':'9.9.9'}, {'reasoning_effort':'low'}, {'resume_trajectory':True},
                  {'mcp_servers':[{}]}, {'model_name':'another-model'}):
        with pytest.raises(ValueError):
            make_agent(tmp_path, cls, **extra)


def test_tool_restrictions_and_native_effort_flags(tmp_path):
    codex = make_agent(tmp_path, CodexNameOnly).build_cli_flags()
    assert 'model_reasoning_effort=max' in codex
    assert 'features.code_mode_host=true' in codex
    assert 'features.code_mode_host=false' not in codex
    for item in ('web_search="disabled"', 'features.apps=false', 'features.multi_agent=false',
                 'features.browser_use=false', 'features.remote_plugin=false'):
        assert item in codex
    claude = make_agent(tmp_path, ClaudeNameOnly).build_cli_flags()
    assert '--effort max' in claude
    assert '--tools Bash,Read,Write,Edit' in claude
    assert '--strict-mcp-config' in claude and '--disable-slash-commands' in claude
    gemini = make_agent(tmp_path, AntigravityNameOnly).build_cli_flags()
    assert '--agent libgen-name-only' in gemini and '--print-timeout 55m' in gemini
    assert 'subagent: false' in AGY_PROFILE and 'inheritMcp: false' in AGY_PROFILE
    # These are advertised in init events but absent from the custom registry.
    assert 'command_status' not in AGY_PROFILE
    assert 'send_command_input' not in AGY_PROFILE
    for tool in ('search_web', 'read_url_content', 'browser_subagent', 'invoke_subagent'):
        assert tool not in AGY_PROFILE
    assert set(PROVIDER_HOSTS) == {'api.openai.com','chatgpt.com','auth.openai.com',
                                   'api.anthropic.com','aiplatform.googleapis.com','oauth2.googleapis.com'}


def test_preflight_failure_stops_before_native_agent_run(tmp_path):
    agent = make_agent(tmp_path, CodexNameOnly)
    agent.exec_as_agent = AsyncMock(return_value=SimpleNamespace(return_code=1, stderr='blocked check failed', stdout=''))
    with pytest.raises(RuntimeError, match='isolation preflight failed'):
        asyncio.run(agent.run('unused', SimpleNamespace(), SimpleNamespace()))
    assert agent.exec_as_agent.await_count == 1
    assert not (tmp_path / 'logs/name-only-isolation.json').exists()


def test_lock_detects_missing_or_changed_files(tmp_path):
    lock = tmp_path / 'experiment.json'
    lock.write_text(json.dumps({'generated_files':{'rules.md':'0'*64}}))
    with pytest.raises(ValueError, match='changed or missing'):
        validate_lock(lock)
    (tmp_path / 'rules.md').write_text('tampered')
    with pytest.raises(ValueError, match='changed or missing'):
        validate_lock(lock)


def test_zero_required_is_na_for_t2_but_keeps_t3():
    assert not metric_eligible('t2_required_family_f1', 0)
    assert metric_eligible('t2_required_family_f1', 1)
    assert metric_eligible('t3_molecular_transition_f1', 0)
    assert metric_eligible('t3_state_f1', 0)


def test_paired_bootstrap_direction_wins_and_constant_difference():
    result = summarize_pairs([(0.7,0.5),(0.6,0.4),(0.9,0.7)], np.random.default_rng(1), 1000)
    assert result['pdf_minus_name_only'] == pytest.approx(0.2)
    assert result['difference_95ci'] == pytest.approx([0.2,0.2])
    assert result['pdf_wins'] == 3 and result['n'] == 3
    result = summarize_pairs([(0.6,0.6),(0.3,0.7),(0.9,0.8)], np.random.default_rng(1), 1000)
    assert [result[k] for k in ('pdf_wins','name_only_wins','ties')] == [1,1,1]


def test_missing_trial_is_not_a_zero_similarity(tmp_path):
    row = score_trial(tmp_path, 'missing', {}, tmp_path, name_only=True)
    assert row['status'] == 'not_run' and row['scores'] is None


def test_outputs_without_isolation_receipt_cannot_enter_control(tmp_path):
    (tmp_path/'result.json').write_text('{"finished_at":"2026-10-11","exception_info":null}')
    row = score_trial(tmp_path, 'missing', {}, tmp_path, name_only=True)
    assert row['status'] == 'isolation_unverified' and row['scores'] is None


@pytest.mark.parametrize('receipt_sha,compatible,status', [
    ('previous', (), 'isolation_unverified'),
    ('previous', ('previous',), 'missing_output'),
    ('unknown', ('previous',), 'isolation_unverified'),
    ('current', (), 'missing_output'),
])
def test_receipt_compatibility_requires_explicit_locked_amendment(tmp_path, receipt_sha, compatible, status):
    (tmp_path / 'result.json').write_text('{"finished_at":"2026-10-11","exception_info":null}')
    (tmp_path / 'agent').mkdir()
    (tmp_path / 'agent/name-only-isolation.json').write_text(json.dumps({
        'ready': True, 'experiment_lock_sha256': receipt_sha,
    }))
    (tmp_path / 'agent/trajectory.json').write_text('{"steps":[]}')
    row = score_trial(tmp_path, 'test', {}, tmp_path, name_only=True,
                      expected_lock_sha='current', compatible_lock_shas=compatible)
    assert row['status'] == status


def test_antigravity_must_discover_restricted_profile_before_inference(tmp_path):
    agent = make_agent(tmp_path, AntigravityNameOnly)
    agent.exec_as_agent = AsyncMock(side_effect=[
        SimpleNamespace(return_code=0, stdout=''),
        SimpleNamespace(return_code=0, stdout=''),
    ])
    with patch.object(NameOnlyMixin, 'install', new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match='did not discover'):
            asyncio.run(agent.install(SimpleNamespace()))
    assert '~/.gemini/config/agents/libgen-name-only/agent.md' in agent.exec_as_agent.call_args_list[0].args[1]


def test_controller_drift_stops_before_model_calls(tmp_path):
    source = tmp_path / 'controller.py'
    source.write_text('changed')
    lock = tmp_path / 'experiment.json'
    lock.write_text(json.dumps({'generated_files': {}, 'controller_files': {str(source): '0'*64}}))
    with pytest.raises(ValueError, match='controller changed'):
        validate_lock(lock)


@pytest.mark.parametrize('message', [
    'Code Mode is unavailable because code-mode host is disabled.',
    'Code Mode is unavailable because codex-code-mode-host could not be started.',
])
def test_codex_host_failure_is_an_execution_error_after_log_download(tmp_path, message):
    agent = make_agent(tmp_path, CodexNameOnly)
    agent.logs_dir.mkdir(exist_ok=True)
    (agent.logs_dir / 'codex.txt').write_text('CLI warning\n' + json.dumps({
        'type': 'item.completed', 'item': {'type': 'error', 'message': message},
    }) + '\n')
    context = SimpleNamespace()
    with patch.object(Codex, 'populate_context_post_run') as populate:
        with pytest.raises(RuntimeError, match='Codex local execution unavailable'):
            agent.populate_context_post_run(context)
        populate.assert_called_once_with(context)
        # Recovery must not rethrow and cancel sibling trials.
        agent.populate_context_post_run(context)


def test_codex_host_failure_detector_ignores_model_text(tmp_path):
    agent = make_agent(tmp_path, CodexNameOnly)
    agent.logs_dir.mkdir(exist_ok=True)
    (agent.logs_dir / 'codex.txt').write_text('\n'.join([
        'ordinary warning', 'null', '[]',
        json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'Code Mode is unavailable is an example error.'}}),
        json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': 'unrelated'}}),
    ]))
    with patch.object(Codex, 'populate_context_post_run'):
        agent.populate_context_post_run(SimpleNamespace())

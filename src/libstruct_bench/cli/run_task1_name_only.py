"""One independent text-only API attempt per Task 1 model/protocol; no retrieval."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
from pathlib import Path

from libstruct_bench.cli import run_openrouter_library_v0 as api
from libstruct_bench.library_structure import LibraryStructureValidationError, extract_json_document
from libstruct_bench.library_structure_policy import OPENROUTER_SOURCE_POLICY

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / 'paper/benchmarks/task1/name_only.json'
CONDITION = 'protocol-name-only (parametric-knowledge control)'
GEMINI = 'google/gemini-3.7-flash'
VERTEX_MODEL = 'gemini-3.7-flash'
LEGACY_RUNNER_SHA256 = 'b560a7f87b028aafca8a0dd2cf8f502220b768fa20ec045b7a91643d007bb42b'
MODELS = {
    'anthropic/claude-opus-5': ('Claude', 'Claude Opus 5'),
    'openai/gpt-5.6-sol': ('GPT', 'GPT-5.6 Sol'),
    'google/gemini-3.7-flash': ('Gemini', 'Gemini 3.7 Flash'),
}


def _study_input(path):
    original = ROOT / path
    if path == "benchmarks/task1/protocols.json" and not original.exists():
        return ROOT / "paper" / path
    return original


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def prompt(protocol):
    # Retain the PDF task's schema, mechanistic reconstruction and placeholder rules.
    # Only grounding instructions change. No case ID/category/reference reaches the API.
    text = api._prompt(protocol_id='protocol_name_only', display_name='', input_paths=[])
    text = text[text.index('Return only JSON with this shape.'):]
    changes = {
        OPENROUTER_SOURCE_POLICY: (
            '- Use only your internal knowledge of the named protocol and version.\n'
            '  Do not browse, search, retrieve documents, consult external sources, or use tools.\n'
            '  No documents or prior conversation are provided.\n'
            '- If you cannot reconstruct any scored final-library sequence, return "libraries": [].'),
        'unless the attached source files support them': 'unless your knowledge of the named protocol supports them',
        'The final library is almost never printed verbatim in the source. Build it by':
            'Reconstruct the final library from your knowledge of the named protocol. Build it by',
        'the building blocks the source actually prints': 'the building blocks you know for this protocol',
        'Record each sequence and its length exactly as written.': 'Recall each sequence and its length as precisely as possible.',
        'a source oligo is given': 'an oligo is known',
        'in\n   a table': 'in\n   an oligo description',
        'Reconstruct enzyme-deposited junctions from source sequence only.':
            'Reconstruct enzyme-deposited junctions from your knowledge of the protocol.',
        'Recover them only from\n   adapter/primer sequence the source provides':
            'Recover them from\n   adapter/primer sequences you know',
        'If the source gives no sequence basis\n   for a constant region':
            'If you do not know the sequence basis\n   for a constant region',
        'rather than\n   supplying remembered bases': 'rather than\n   inventing bases',
        'Ground every length in the source. Take each barcode, UMI, and index length\n   from an explicit source statement (read structure, oligo diagram, or table),\n   never from a default or a remembered construct.':
            'Recall each barcode, UMI, and index length for the specified protocol\n   and version; do not substitute a default from another protocol or version.',
        'configuration the source describes': 'configuration you know for this protocol',
        'what the source says Read 1 reads': 'what you know Read 1 reads',
        'not source evidence. If any source-derived sequence conflicts with the\n  example, the source wins.':
            'not protocol information. If a recalled sequence conflicts with the\n  example, use the recalled sequence.',
        'when the source shows them': 'when you know them',
        'source confirms a final library exists': 'protocol has a final library',
        'from the input files': 'from your internal knowledge',
        'If the source shows unknown biological payload': 'If a recalled construct contains unknown biological payload',
        'explicit source-visible nucleotide/IUPAC motif letters': 'known nucleotide/IUPAC motif letters',
        'Source terms seen in the ground truth include': 'Examples of structural role terms include',
        'If the source gives a range': 'If the protocol specifies a range',
        'a specific source oligo': 'a specific protocol oligo',
        'the source describes': 'you know for this protocol',
        'a source-grounded\n  length': 'a recalled protocol-specific\n  length',
    }
    for before, after in changes.items():
        if before not in text:
            raise ValueError(f'Task 1 prompt changed; review name-only adaptation: {before!r}')
        text = text.replace(before, after)
    version = protocol['version'] or 'not separately specified'
    return (f"Protocol name: {protocol['canonical_name']}\nProtocol version: {version}\n\n"
            "Reconstruct the final sequencing library structure from internal knowledge\n"
            "as one 5'->3' sequence entry per final library.\n\n" + text)


def payload(protocol, model, provider='openrouter'):
    result = api._chat_payload(model=model, prompt=prompt(protocol), file_refs=[], pdf_engine='',
                               max_completion_tokens=64000, temperature=None, reasoning_effort='high')
    result.update(tools=[], tool_choice='none', plugins=[
        {'id': name, 'enabled': False} for name in ('web', 'file-parser', 'response-healing')])
    if provider == 'vertex':
        if model != GEMINI:
            raise ValueError('Vertex route is only configured for the matched Gemini model')
        return {
            'systemInstruction': {'parts': [{'text': result['messages'][0]['content']}]},
            'contents': [{'role': 'user', 'parts': [{'text': prompt(protocol)}]}],
            'tools': [],
            'generationConfig': {'candidateCount': 1, 'maxOutputTokens': 64000,
                                 'responseMimeType': 'application/json',
                                 'thinkingConfig': {'thinkingLevel': 'HIGH', 'includeThoughts': False}},
        }
    if provider != 'openrouter':
        raise ValueError(f'Unknown provider: {provider}')
    return result


def load_config(path=DEFAULT_CONFIG, *, verify_locked_files=True):
    config = read(path)
    if config['condition'] != CONDITION or config['api_defaults'] != {
        'models': list(MODELS), 'reasoning_effort': 'high', 'max_completion_tokens': 64000,
    }:
        raise ValueError('Unexpected name-only condition or API settings')
    for item in config['locked_files'] if verify_locked_files else []:
        if sha(_study_input(item['path'])) != item['sha256']:
            raise ValueError(f"Locked input changed: {item['path']}")
    round2 = read(_study_input(config['round2_manifest']))
    ids = [p['protocol_id'] for p in config['protocols']]
    if len(ids) != 20 or len(set(ids)) != 20 or ids != [p['protocol_id'] for p in round2['protocols']]:
        raise ValueError('Expected exactly the round-2 protocols, in manifest order')
    for p in config['protocols']:
        if set(p) != {'protocol_id', 'canonical_name', 'version', 'method_group'}:
            raise ValueError('Name-only protocol entries must not carry evidence/extra context')
        if not isinstance(p['canonical_name'], str) or not p['canonical_name'].strip():
            raise ValueError('Canonical protocol name required')
        if p['version'] is not None and not isinstance(p['version'], str):
            raise ValueError('Protocol version must be a string or null')
        if p['method_group'] not in ('commercial', 'academic'):
            raise ValueError('Invalid exploratory method group')
    return config


def experiment_lock(config, vertex_project=None):
    result = {
        'condition': CONDITION, 'manifest': config, 'attempts_per_case': 1,
        'transport': api.OPENROUTER_CHAT_URL,
        'implementation_sha256': {'runner': sha(__file__), 'api_helpers': sha(api.__file__)},
        'prompt_sha256': {p['protocol_id']: hashlib.sha256(prompt(p).encode()).hexdigest()
                          for p in config['protocols']},
    }
    if vertex_project:
        result['gemini_transport'] = {'provider': 'vertex', 'project': vertex_project,
                                     'location': 'global', 'model': VERTEX_MODEL,
                                     'endpoint': vertex_url(vertex_project)}
    return result


def validate_experiment(out, config):
    saved = read(out / 'experiment.json')
    project = saved.get('gemini_transport', {}).get('project')
    current = {k: v for k, v in saved.items() if k != 'amendments'}
    if current != experiment_lock(config, project):
        raise ValueError('Output experiment lock differs; refusing to mix conditions or revisions')
    for amendment in saved.get('amendments', []):
        for relative, digest in amendment['preserved_attempt_files'].items():
            if not (out / relative).is_file():
                raise ValueError(f'Preserved pre-amendment artifact missing: {relative}. '
                                 'Restore the original files or record an explicit run restart; '
                                 'deleting results does not make these unstarted cases.')
            if sha(out / relative) != digest:
                raise ValueError(f'Preserved pre-amendment artifact changed: {relative}')
    return saved


def prepare_directory(out, config, vertex_project=None):
    lock = out / 'experiment.json'
    if lock.exists():
        saved = read(lock)
        project = vertex_project or saved.get('gemini_transport', {}).get('project')
        expected = experiment_lock(config, project)
        if {k: v for k, v in saved.items() if k != 'amendments'} != expected:
            legacy = experiment_lock(config)
            legacy['implementation_sha256']['runner'] = LEGACY_RUNNER_SHA256
            if not vertex_project or saved not in (legacy, experiment_lock(config)):
                raise ValueError('Output experiment lock differs; refusing to mix conditions or revisions')
            # Only the known pre-Vertex implementation can be migrated; requests/predictions stay intact.
            expected['amendments'] = [{
                'reason': 'User requested Google Cloud ADC for remaining Gemini cases and a Docker sandbox.',
                'recorded_at_unix': time.time(), 'previous_experiment': saved,
                'previous_experiment_sha256': sha(lock),
                'preserved_attempt_files': {str(p.relative_to(out)): sha(p)
                                            for p in sorted(out.glob('*/*/*'))
                                            if p.is_file() and (p.parent / 'attempt.json').exists()},
            }]
            write(lock, expected)
    else:
        if out.exists() and any(out.iterdir()):
            raise ValueError('Refusing to use a nonempty directory without the name-only experiment lock')
        out.mkdir(parents=True, exist_ok=True)
        expected = experiment_lock(config, vertex_project)
        # Exclusive creation also prevents two launches from replacing each other's lock.
        with lock.open('x') as stream:
            json.dump(expected, stream, indent=2, sort_keys=True)
            stream.write('\n')
    return validate_experiment(out, config)


def vertex_url(project):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]', project):
        raise ValueError('A Google Cloud project ID is required')
    return (f'https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/'
            f'publishers/google/models/{VERTEX_MODEL}:generateContent')


def adc_file(project):
    path = Path(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or
                Path(os.environ.get('CLOUDSDK_CONFIG', str(Path.home() / '.config/gcloud'))) /
                'application_default_credentials.json').expanduser()
    credentials = read(path)
    if (credentials.get('type') != 'authorized_user' or credentials.get('quota_project_id') != project
            or not all(credentials.get(k) for k in ('client_id', 'client_secret', 'refresh_token'))):
        raise ValueError(f'Expected user ADC for {project}; run gcloud auth application-default login '
                         f'--project {project}, then gcloud auth application-default set-quota-project {project}')
    return path


def vertex_access_token(project):
    credentials = read(adc_file(project))
    form = {k: credentials[k] for k in ('client_id', 'client_secret', 'refresh_token')}
    form['grant_type'] = 'refresh_token'
    request = api.urllib.request.Request('https://oauth2.googleapis.com/token',
        data=api.urllib.parse.urlencode(form).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    try:
        with api.urllib.request.urlopen(request, timeout=60) as response:
            token = json.loads(response.read()).get('access_token')
    except urllib.error.HTTPError as exc:
        raise api.OpenRouterResponseError(f'Google ADC refresh failed (HTTP {exc.code}); renew ADC login') from None
    if not isinstance(token, str) or not token:
        raise api.OpenRouterResponseError('Google ADC refresh returned no access token')
    return token  # Never saved, logged, or sent to the model.


def post_vertex(project, request_payload):
    request = api.urllib.request.Request(vertex_url(project), data=json.dumps(request_payload).encode(),
        headers={'Authorization': 'Bearer ' + vertex_access_token(project),
                 'x-goog-user-project': project, 'Content-Type': 'application/json'}, method='POST')
    try:
        with api.urllib.request.urlopen(request, timeout=600) as response:
            body = response.read().decode('utf-8', errors='replace')
            try:
                return json.loads(body)
            except ValueError:
                raise api.OpenRouterResponseError('Vertex returned a non-JSON transport response', body=body) from None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise api.OpenRouterResponseError(f'Vertex HTTP {exc.code}: {body}', body=body, status_code=exc.code) from None


def interpret_vertex(response, model, pid):
    if not isinstance(response, dict) or response.get('error'):
        return {'status': 'technical_failure', 'error': 'Vertex provider error: ' + str(response)}, None, None
    version = response.get('modelVersion', '')
    if model != GEMINI or not re.fullmatch(re.escape(VERTEX_MODEL) + r'(?:-\d{3})?', version):
        return {'status': 'condition_violation', 'error': f'Unexpected Vertex model version: {version}'}, None, None
    candidates = response.get('candidates') or []
    if not candidates and response.get('promptFeedback', {}).get('blockReason'):
        return {'status': 'invalid_output', 'error': 'Vertex blocked the prompt'}, None, ''
    if len(candidates) != 1:
        return {'status': 'technical_failure', 'error': 'Expected one Vertex candidate'}, None, None
    candidate = candidates[0]
    parts = candidate.get('content', {}).get('parts', [])
    # citationMetadata alone can be a recitation citation from parametric knowledge, not retrieval.
    if (candidate.get('groundingMetadata') or candidate.get('urlContextMetadata') or
            any(any(k in p for k in ('functionCall', 'functionResponse', 'executableCode', 'codeExecutionResult')) for p in parts)):
        return {'status': 'condition_violation', 'error': 'Vertex response reports tools or grounding'}, None, None
    text = '\n'.join(p['text'] for p in parts if isinstance(p.get('text'), str) and not p.get('thought'))
    reason = candidate.get('finishReason', '')
    normalized = {'model': model, 'choices': [{'message': {'content': text},
                    'finish_reason': 'stop' if reason in ('STOP', 'FINISH_REASON_STOP') else reason}]}
    return interpret_response(normalized, model, pid)


def interpret_response(response, model, protocol_id):
    """Distinguish transport failures from actual model-output failures; never retry."""
    if not isinstance(response, dict) or response.get('error'):
        return {'status': 'technical_failure', 'error': str(response.get('error') if isinstance(response, dict) else response)}, None, None
    if response.get('model') != model:
        return {'status': 'condition_violation', 'error': 'Returned model does not match requested model'}, None, None
    choices = response.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        return {'status': 'technical_failure', 'error': 'Expected one response choice'}, None, None
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get('error') or choice.get('finish_reason') == 'error':
        return {'status': 'technical_failure', 'error': f'Provider choice error: {choice}'}, None, None
    message = choice.get('message') or {}
    if not isinstance(message, dict):
        return {'status': 'technical_failure', 'error': 'Malformed provider message envelope'}, None, None
    usage = response.get('usage') or {}
    if (message.get('tool_calls') or message.get('function_call') or message.get('annotations')
            or choice.get('finish_reason') in ('tool_calls', 'function_call')
            or (usage.get('server_tool_use') or {}).get('web_search_requests', 0)):
        return {'status': 'condition_violation', 'error': 'Response reports tools, annotations or search usage'}, None, None
    try:
        text = api._response_text(response)
    except api.OpenRouterResponseError:
        text = ''
    # A generated but truncated/refused/malformed answer is an output failure, not missing data.
    if choice.get('finish_reason') != 'stop':
        return {'status': 'invalid_output', 'error': f"finish_reason={choice.get('finish_reason')!r}"}, None, text
    try:
        parsed = extract_json_document(text)
        prediction = api._prediction_document(parsed, protocol_id)
    except (LibraryStructureValidationError, ValueError, TypeError, AttributeError) as exc:
        return {'status': 'invalid_output', 'error': str(exc)}, None, text
    return {'status': 'completed', 'error': ''}, prediction, text


def run_one(out, protocol, model, api_key, dry_run=False, vertex_project=None, sandboxed=False):
    pid = protocol['protocol_id']
    case = out / api._slug(model) / pid
    case.mkdir(parents=True, exist_ok=True)
    request_path = case / 'request.json'
    if (case / 'attempt.json').exists():
        previous = read(case / 'attempt.json')
        if read(request_path) != payload(protocol, model, previous.get('provider', 'openrouter')):
            raise ValueError(f'Request changed: {case}')
        return {'model': model, 'protocol_id': pid, 'status': 'skipped_existing_attempt'}
    provider = 'vertex' if model == GEMINI and vertex_project else 'openrouter'
    request = payload(protocol, model, provider)
    if request_path.exists() and read(request_path) != request:
        # A preview consumes no attempt; permit the explicitly recorded transport amendment.
        if provider != 'vertex' or read(request_path) != payload(protocol, model):
            raise ValueError(f'Request changed: {case}')
    write(request_path, request)
    if dry_run:
        return {'model': model, 'protocol_id': pid, 'status': 'dry_run'}
    attempt = {'model': model, 'protocol_id': pid, 'started_at_unix': time.time(), 'status': 'started',
               'provider': provider, 'project': vertex_project if provider == 'vertex' else None,
               'sandboxed': sandboxed}
    # Never resend an uncertain/interrupted attempt or select the best of multiple answers.
    with (case / 'attempt.json').open('x') as stream:
        json.dump(attempt, stream, indent=2)
    try:
        print(f'  Sending via {provider}; waiting for one complete response (high reasoning).', flush=True)
        response = (post_vertex(vertex_project, request) if provider == 'vertex'
                    else api._post_json(api.OPENROUTER_CHAT_URL, api_key, request))
        write(case / 'response.json', response)
        interpreter = interpret_vertex if provider == 'vertex' else interpret_response
        result, prediction, text = interpreter(response, model, pid)
        if text is not None:
            (case / 'raw_response.txt').write_text(text)
        if prediction is not None:
            write(case / 'prediction.json', prediction)
    except (api.OpenRouterResponseError, urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        result = {'status': 'technical_failure', 'error': str(exc)}
        if isinstance(exc, api.OpenRouterResponseError) and exc.body is not None:
            (case / 'failed_response.txt').write_text(exc.body)
    except KeyboardInterrupt:
        write(case / 'result.json', {**attempt, 'status': 'interrupted', 'error': 'Interrupted; request outcome may be unknown'})
        raise
    result = {**attempt, **result, 'finished_at_unix': time.time()}
    write(case / 'result.json', result)
    print(f"  {result['status']} after {result['finished_at_unix'] - attempt['started_at_unix']:.0f}s", flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocols', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--out', type=Path, default=ROOT / 'runs/library_structure/round2/name_only')
    parser.add_argument('--model', action='append', choices=list(MODELS),
                        help='Run only this matched model; repeat to select multiple. Defaults to all three.')
    parser.add_argument('--dry-run', action='store_true', help='Save selected text-only requests; no network or attempt consumed')
    parser.add_argument('--gemini-provider', choices=['openrouter', 'vertex'],
                        help='Gemini API route. Defaults to the route recorded in this run, initially OpenRouter.')
    parser.add_argument('--gcp-project', help='Google Cloud billing/quota project for Vertex; uses user ADC, not an API key')
    parser.add_argument('--docker', action='store_true', help='Isolate runner in Docker with allowlisted API egress')
    parser.add_argument('--check-sandbox', action='store_true', help='With --docker: test isolation/egress, without inference or attempts')
    parser.add_argument('--sandbox-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sandbox_worker and os.environ.get('TASK1_DOCKER_WORKER') != '1':
        parser.error('--sandbox-worker is reserved for the Docker launcher')
    if args.check_sandbox and not args.docker:
        parser.error('--check-sandbox requires --docker')
    config = load_config(args.protocols, verify_locked_files=not args.sandbox_worker)
    models = list(dict.fromkeys(args.model or MODELS))
    saved = read(args.out / 'experiment.json') if (args.out / 'experiment.json').exists() else {}
    recorded_project = saved.get('gemini_transport', {}).get('project')
    project = args.gcp_project or recorded_project
    if args.gemini_provider == 'openrouter' and project:
        parser.error('Cannot switch a recorded Vertex route back to OpenRouter in this experiment')
    if args.gemini_provider == 'vertex' and not project:
        parser.error('--gemini-provider vertex requires --gcp-project on first use')
    if project:
        vertex_url(project)
    pending = [m for m in models if any(not (args.out / api._slug(m) / p['protocol_id'] / 'attempt.json').exists()
                                       for p in config['protocols'])]
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not args.dry_run and not args.check_sandbox and not api_key and any(m != GEMINI or not project for m in pending):
        parser.error('Export OPENROUTER_API_KEY or use --dry-run')
    if project and GEMINI in pending and not args.dry_run and not args.check_sandbox:
        adc_file(project)
    prepare_directory(args.out, config, project)
    if args.docker:
        from libstruct_bench.cli.task1_name_only_sandbox import launch
        return launch(args.out, args.protocols, models, project, args.dry_run, args.check_sandbox)
    total = len(config['protocols']) * len(models)
    print(f'{CONDITION}: {len(config["protocols"])} protocols × {len(models)} model(s); '
          'high; 64,000 completion tokens; one attempt each.', flush=True)
    print('Dry run: no API calls.' if args.dry_run else 'Existing attempts are skipped. No automatic retries.', flush=True)
    summary_path = args.out / 'summary.json'
    previous = read(summary_path) if summary_path.exists() else []
    rows = {(r['model'], r['protocol_id']): r for r in previous}
    index = 0
    for model in models:
        for protocol in config['protocols']:
            index += 1
            print(f"[{index}/{total}] {model} | {protocol['protocol_id']}", flush=True)
            result = run_one(args.out, protocol, model, api_key, args.dry_run, project, args.sandbox_worker)
            key = (model, protocol['protocol_id'])
            if result['status'] != 'skipped_existing_attempt' or key not in rows:
                rows[key] = result
            write(summary_path, list(rows.values()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

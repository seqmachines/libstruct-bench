"""Build a minimal Docker runner and an HTTPS allowlist proxy; no model tools."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from libstruct_bench.cli import run_task1_name_only as run

SOURCE_FILES = (
    '__init__.py', 'matching.py', 'library_structure.py', 'library_structure_policy.py',
    'library_structure_inputs.py', 'hf_io.py', 'cli/__init__.py',
    'cli/run_openrouter_library_v0.py', 'cli/run_task1_name_only.py',
)
PROXY_SOURCE = run.ROOT / 'benchmarks/molecular_workflow_reconstruction/docker_network/provider_egress_proxy.py'
IMAGE = 'libstruct-task1-name-only:local'

# This check makes no generation request and consumes no attempt. A denied direct
# socket plus a successful allowlisted TLS request distinguishes isolation from a
# completely broken network. The proxy itself denies non-allowlisted hosts.
SMOKE = r'''
import json, os, socket, urllib.error, urllib.request
from pathlib import Path
assert os.getuid() != 0, 'Runner must be non-root'
assert not Path('/var/run/docker.sock').exists()
assert not Path('/app/runs').exists()
assert not Path('/app/analysis').exists()
assert not Path('/app/benchmarks/task1/tasks').exists()
try:
    Path('/app/should-not-write').write_text('test')
except OSError:
    pass
else:
    raise AssertionError('Image filesystem is writable')
try:
    connection = socket.create_connection(('1.1.1.1', 443), timeout=3)
except OSError:
    pass
else:
    connection.close()
    raise AssertionError('Direct public egress is available')
try:
    urllib.request.urlopen('https://example.com', timeout=10)
except urllib.error.URLError as exc:
    assert '403' in str(exc), 'Expected proxy deny, got: ' + str(exc)
else:
    raise AssertionError('Non-allowlisted host was reachable')
host = os.environ['TASK1_SMOKE_HOST']
try:
    with urllib.request.urlopen('https://' + host + '/', timeout=30) as response:
        status = response.status
except urllib.error.HTTPError as exc:
    status = exc.code  # HTTP errors still prove the TLS connection succeeded.
except urllib.error.URLError as exc:
    raise AssertionError('Allowlisted TLS endpoint unreachable: ' + str(exc))
print(json.dumps({'sandbox_check': 'passed', 'uid': os.getuid(),
                 'readonly_image': True, 'direct_egress_blocked': True,
                 'non_allowlisted_host_blocked': True,
                 'allowed_host': host, 'allowed_host_http_status': status,
                 'inference_calls': 0}), flush=True)
'''


def context(directory, config_path):
    """Explicit file allowlist: never send the repository as a Docker build context."""
    sources = {f'src/libstruct_bench/{name}': run.ROOT / 'src/libstruct_bench' / name
               for name in SOURCE_FILES}
    sources.update({
        'paper/benchmarks/task1/name_only.json': Path(config_path),
        'benchmarks/task1/protocols.json': run.ROOT / 'paper/benchmarks/task1/protocols.json',
        'proxy/provider_egress_proxy.py': PROXY_SOURCE,
    })
    hashes = {}
    for relative, source in sources.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative] = run.sha(source)
    (directory / 'Dockerfile').write_text(
        'FROM python:3.13-slim\nWORKDIR /app\n'
        'ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n'
        'COPY src /app/src\nCOPY benchmarks /app/benchmarks\nCOPY paper /app/paper\nCOPY proxy /proxy\n'
        'RUN mkdir -p /logs/network\n')
    return hashes


def compose_spec(directory, out, models, project, dry_run, credentials=None, check_only=False):
    vertex = bool(project and run.GEMINI in models)
    openrouter = any(m != run.GEMINI or not project for m in models)
    hosts = (['aiplatform.googleapis.com', 'oauth2.googleapis.com'] if vertex else [])
    if openrouter:
        hosts.append('openrouter.ai')
    policy = directory / 'policy.json'
    run.write(policy, {'provider_hosts': hosts, 'setup_hosts': []})
    command = ['python', '-m', 'libstruct_bench.cli.run_task1_name_only',
               '--sandbox-worker', '--out', '/runs']
    for model in models:
        command.extend(['--model', model])
    if project:
        command.extend(['--gemini-provider', 'vertex', '--gcp-project', project])
    if dry_run:
        command.append('--dry-run')
    environment = {'TASK1_DOCKER_WORKER': '1', 'HTTP_PROXY': 'http://provider-egress:3128',
                   'HTTPS_PROXY': 'http://provider-egress:3128', 'NO_PROXY': 'localhost,127.0.0.1',
                   'TASK1_SMOKE_HOST': hosts[0]}
    volumes = [{'type': 'bind', 'source': str(out.resolve()), 'target': '/runs'}]
    if credentials is not None:
        volumes.append({'type': 'bind', 'source': str(credentials.resolve()),
                        'target': '/credentials/adc.json', 'read_only': True})
        environment['GOOGLE_APPLICATION_CREDENTIALS'] = '/credentials/adc.json'
    if openrouter and not dry_run and not check_only:
        # The value is passed at launch; no credential is written into the build or receipt.
        environment['OPENROUTER_API_KEY'] = '${OPENROUTER_API_KEY:?Export OPENROUTER_API_KEY}'
    common = {'image': IMAGE, 'read_only': True, 'user': f'{os.getuid()}:{os.getgid()}',
              'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
              'pids_limit': 64, 'mem_limit': '512m', 'cpus': 1.0,
              'tmpfs': ['/tmp:rw,noexec,nosuid,size=32m,mode=1777']}
    # Pin the tested base image digest via the built image ID in the receipt.
    return {'services': {
        'runner': {**common, 'build': {'context': str(directory)}, 'command': command,
                   'environment': environment, 'volumes': volumes, 'networks': ['isolated'],
                   'depends_on': {'provider-egress': {'condition': 'service_healthy'}}},
        'provider-egress': {**common,
            'command': ['python', '/proxy/provider_egress_proxy.py', '/proxy/policy.json'],
            'networks': ['isolated', 'egress'],
            'volumes': [
                {'type': 'bind', 'source': str(policy), 'target': '/proxy/policy.json', 'read_only': True},
                {'type': 'bind', 'source': str(out.resolve() / 'provider-egress.jsonl'),
                 'target': '/logs/network/provider-egress.jsonl'}],
            'healthcheck': {'test': ['CMD', 'python', '-c',
                "import socket; socket.create_connection(('127.0.0.1',3128),1).close()"],
                'interval': '2s', 'timeout': '2s', 'retries': 20}},
        }, 'networks': {'isolated': {'internal': True}, 'egress': {}}}


def launch(out, config_path, models, project, dry_run=False, check_only=False):
    out = out.resolve()
    credentials = run.adc_file(project) if project and run.GEMINI in models and not (dry_run or check_only) else None
    if os.getuid() == 0:
        raise ValueError('Launch as a regular user so the container also runs non-root')
    with tempfile.TemporaryDirectory(prefix='task1-name-only-docker-') as temp:
        directory = Path(temp)
        hashes = context(directory, config_path)
        spec = compose_spec(directory, out, models, project, dry_run, credentials, check_only)
        path = directory / 'compose.json'
        run.write(path, spec)
        (out / 'provider-egress.jsonl').touch(exist_ok=True)
        command = ['docker', 'compose', '-p', 'task1-name-only-' + uuid.uuid4().hex[:10], '-f', str(path)]
        subprocess.run([*command, 'build', 'runner'], check=True)
        image_id = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
        # Resolve the mutable local tag before launch, so parallel builds cannot switch our image.
        for service in spec['services'].values():
            service['image'] = image_id
            service.pop('build', None)
        run.write(path, spec)
        receipt_path = out / 'sandbox.json'
        receipts = run.read(receipt_path) if receipt_path.exists() else []
        receipt = {'started_at_unix': time.time(), 'image_id': image_id, 'source_sha256': hashes,
                   'launcher_sha256': run.sha(__file__), 'models': models, 'project': project,
                   'dry_run': dry_run, 'check_only': check_only,
                   'allowed_hosts': run.read(directory / 'policy.json')['provider_hosts'],
                   'readonly_image': True, 'non_root': True, 'runner_network_internal': True,
                   'writable_mount': str(out), 'credential_file_mounted_readonly': credentials is not None,
                   'status': 'starting'}
        receipts.append(receipt)
        run.write(receipt_path, receipts)
        try:
            subprocess.run([*command, 'up', '-d', '--wait', 'provider-egress'], check=True)
            print('Checking sandbox isolation; no inference calls.', flush=True)
            smoke = subprocess.run([*command, 'run', '--rm', '--no-deps', '-T', 'runner',
                                    'python', '-c', SMOKE], check=True, capture_output=True, text=True)
            receipt['checks'] = json.loads(smoke.stdout.strip().splitlines()[-1])
            run.write(receipt_path, receipts)
            print(smoke.stdout.strip(), flush=True)
            if not check_only:
                result = subprocess.run([*command, 'run', '--rm', '--no-deps', '-T', 'runner'])
                receipt['exit_code'] = result.returncode
                receipt['status'] = 'finished' if result.returncode == 0 else 'failed'
                return result.returncode
            receipt['status'] = 'check_passed'
            return 0
        except subprocess.CalledProcessError as exc:
            receipt['status'] = 'failed'
            if exc.stderr:
                print(exc.stderr, flush=True)
            raise
        except KeyboardInterrupt:
            receipt['status'] = 'interrupted'
            raise
        finally:
            receipt['finished_at_unix'] = time.time()
            run.write(receipt_path, receipts)
            subprocess.run([*command, 'down', '--timeout', '10'], check=False)

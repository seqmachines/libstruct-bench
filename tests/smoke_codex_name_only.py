"""Exercise the pinned Codex tool runtime with an offline Responses stub.

Run inside the disposable name-only agent container after installing Codex
0.147.0. Pass JSON argv exported from CodexNameOnly.build_cli_flags(). No auth,
external network, model inference, protocol knowledge, or benchmark score is
used. The negative control reproduces the disabled-host failure.
"""
from __future__ import annotations

import argparse
import http.server
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading


def run_case(flags: list[str], *, disabled: bool) -> dict:
    requests = []
    with tempfile.TemporaryDirectory(prefix='codex-host-smoke-') as directory:
        marker = Path(directory) / 'marker.json'
        script = (
            "import json; from pathlib import Path; from jsonschema import validate; "
            "assert Path('/workspace/rules.md').read_text(); "
            f"p=Path({str(marker)!r}); p.write_text(json.dumps({{'smoke':'code-mode-ok'}})); "
            "data=json.loads(p.read_text()); "
            "validate(data, {'type':'object','required':['smoke'],"
            "'properties':{'smoke':{'const':'code-mode-ok'}},'additionalProperties':False}); "
            "print(data['smoke'])"
        )
        tool_input = 'text(await tools.exec_command(' + json.dumps({
            'cmd': 'python -c ' + shlex.quote(script),
        }) + '));'

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                if len(requests) == 1:
                    item = dict(type='custom_tool_call', id='ctc_smoke', call_id='call_smoke',
                                name='exec', input=tool_input)
                else:
                    item = dict(type='message', id='msg_smoke', role='assistant', status='completed',
                                content=[dict(type='output_text', text='Smoke complete.')])
                response = dict(id=f'resp_{len(requests)}', object='response', status='completed',
                                output=[item], usage=dict(input_tokens=1, output_tokens=1, total_tokens=2))
                events = [
                    dict(type='response.created', response=dict(response, status='in_progress', output=[])),
                    dict(type='response.output_item.added', output_index=0, item=item),
                    dict(type='response.output_item.done', output_index=0, item=item),
                    dict(type='response.completed', response=response),
                ]
                body = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            command = ['codex', 'exec', '--dangerously-bypass-approvals-and-sandbox',
                       '--skip-git-repo-check', '--model', 'gpt-5.6-sol', '--json',
                       '--enable', 'unified_exec', *flags,
                       '-c', 'model_provider="smoke"',
                       '-c', 'model_providers.smoke.name="Offline smoke"',
                       '-c', f'model_providers.smoke.base_url="http://127.0.0.1:{server.server_port}/v1"',
                       '-c', 'model_providers.smoke.wire_api="responses"',
                       '-c', 'model_providers.smoke.requires_openai_auth=false',
                       '-c', 'features.enable_request_compression=false']
            if disabled:
                command += ['-c', 'features.code_mode_host=false']
            result = subprocess.run(command + ['--', 'Read, write, and validate the smoke marker.'],
                                    capture_output=True, text=True, timeout=60)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        output = result.stdout + result.stderr
        if disabled:
            assert not marker.exists(), output
            assert 'code-mode host is disabled' in output, output
        else:
            assert result.returncode == 0, output
            assert marker.is_file(), output
            assert json.loads(marker.read_text()) == {'smoke': 'code-mode-ok'}
            assert '"exit_code":0' in result.stdout, output
            assert 'Code Mode is unavailable' not in output, output
        assert len(requests) == 2, output
        return dict(disabled_host=disabled, marker_valid=marker.is_file(),
                    cli_returncode=result.returncode, stub_requests=len(requests))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flags-json', required=True, type=Path)
    args = parser.parse_args()
    version = subprocess.check_output(['codex', '--version'], text=True).strip()
    assert version == 'codex-cli 0.147.0', version
    flags = json.loads(args.flags_json.read_text())
    assert 'features.code_mode_host=true' in flags
    print(json.dumps(dict(version=version, cases=[
        run_case(flags, disabled=True), run_case(flags, disabled=False),
    ]), indent=2))


if __name__ == '__main__':
    main()

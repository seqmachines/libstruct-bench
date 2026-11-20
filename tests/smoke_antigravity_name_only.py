"""Offline Antigravity 1.3.2 custom-profile regression.

Run ONLY as root in a disposable Linux container with external networking
removed, the pinned agy binary installed, and /workspace available:
  python smoke_antigravity_name_only.py /tmp/production-agent.md
The profile argument must be exported from name_only.AGY_PROFILE. Local fake
ADC and Vertex servers supply deterministic responses, never real model calls.
"""
import sys
from pathlib import Path
assert Path('/.dockerenv').exists(), 'Use a disposable Docker container'
assert len(Path('/proc/net/route').read_text().splitlines()) == 1, 'Disconnect external networking first'
import json, os, subprocess, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
class TokenHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        body=json.dumps({'access_token':'offline-placeholder-not-a-real-token', 'token_type':'Bearer','expires_in':3600,'issued_token_type':'urn:ietf:params:oauth:token-type:access_token'}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass
server=HTTPServer(('127.0.0.1',0),TokenHandler)
threading.Thread(target=server.serve_forever,daemon=True).start()
from pathlib import Path
Path('/tmp/offline-subject').write_text('offline-placeholder')
Path('/tmp/offline-adc.json').write_text(json.dumps({'type':'external_account','audience':'//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/offline/providers/offline','subject_token_type':'urn:ietf:params:oauth:token-type:jwt','token_url':f'http://127.0.0.1:{server.server_port}/token','credential_source':{'file':'/tmp/offline-subject'},'quota_project_id':'offline-smoke'}))
os.environ['GOOGLE_APPLICATION_CREDENTIALS']='/tmp/offline-adc.json'

import ssl
cert=Path('/tmp/offline-cert.pem'); key=Path('/tmp/offline-key.pem')
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=aiplatform.googleapis.com','-addext','subjectAltName=DNS:aiplatform.googleapis.com'],check=True,capture_output=True)
with Path('/etc/hosts').open('a') as f: f.write('\n127.0.0.1 aiplatform.googleapis.com\n')
os.environ['SSL_CERT_FILE']=str(cert)
class ModelHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get('Transfer-Encoding','').lower() == 'chunked':
            chunks=[]
            while True:
                size=int(self.rfile.readline().split(b';')[0],16)
                if size == 0:
                    self.rfile.readline(); break
                chunks.append(self.rfile.read(size)); self.rfile.read(2)
            raw=b''.join(chunks)
        else:
            raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
        request=json.loads(raw)
        Path('/tmp/model-request.json').write_text(json.dumps(request,indent=2))
        tools=[t['name'] for g in request.get('tools',[]) for t in g.get('functionDeclarations',[])]
        assert set(tools) <= {'run_command','view_file','write_to_file','replace_file_content','multi_replace_file_content','manage_task'},tools
        if 'run_command' in tools and not getattr(self.server,'requested_tool',False):
            self.server.requested_tool=True
            parts=[{'functionCall':{'name':'run_command','args':{'CommandLine':"python -c \"import json; from pathlib import Path; p=Path('/workspace/profile-smoke.json'); p.write_text(json.dumps({'ok':True})); assert json.loads(p.read_text())=={'ok':True}; print('LOCAL_TOOL_OK')\"",'Cwd':'/workspace','WaitMsBeforeAsync':1000,'toolSummary':'Local file test','toolAction':'Testing local files'}}}]
        else:
            parts=[{'text':'Offline fixture complete.'}]
        body={'candidates':[{'content':{'role':'model','parts':parts},'finishReason':'STOP'}], 'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':5,'totalTokenCount':15}}
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers(); self.wfile.write(('data: '+json.dumps(body)+'\n\n').encode())
    def log_message(self,*args): pass
model_server=HTTPServer(('127.0.0.1',443),ModelHandler)
ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ssl_context.load_cert_chain(cert,key)
model_server.socket=ssl_context.wrap_socket(model_server.socket,server_side=True)
threading.Thread(target=model_server.serve_forever,daemon=True).start()

corrected = Path(sys.argv[1]).read_text()
assert 'command_status' not in corrected and 'send_command_input' not in corrected
old = corrected.replace('  - run_command\n', '  - run_command\n  - command_status\n  - send_command_input\n')
marker = Path('/workspace/profile-smoke.json')
marker.unlink(missing_ok=True)
profile = Path('/root/.gemini/config/agents/libgen-name-only/agent.md')
profile.parent.mkdir(parents=True,exist_ok=True)
for label, content in [('original', old), ('corrected', corrected)]:
    profile.write_text(content)
    result = subprocess.run(['/root/.local/bin/agy', '--agent', 'libgen-name-only', '--model', 'gemini-3.7-flash-high', '--effort','high','--dangerously-skip-permissions', '--disable-slash-commands', '--output-format','stream-json', '--print-timeout','15s','-p', 'Write /tmp/profile-smoke.txt containing OK using run_command, then read it back.'], env={**os.environ, 'AGY_ADC_AUTH':'true', 'AGY_CLI_DISABLE_AUTO_UPDATE':'true', 'GOOGLE_CLOUD_PROJECT':'offline-smoke', 'GOOGLE_CLOUD_QUOTA_PROJECT':'offline-smoke'},text=True,capture_output=True,timeout=25)
    Path('/tmp/profile-'+label+'.log').write_text(result.stdout+'\n'+result.stderr)
    events=[]
    for line in result.stdout.splitlines():
        try:
            event=json.loads(line)
        except ValueError:
            continue
        if event.get('event')=='result': events.append(event['result'])
    assert events, result.stdout + result.stderr
    outcome=events[-1]
    if label=='original':
        assert 'failed to construct executor' in outcome.get('error',''), outcome
        assert not marker.exists()
    else:
        assert result.returncode==0 and outcome['status']=='SUCCESS', outcome
        assert json.loads(marker.read_text())=={'ok':True}
    print(label, 'exit',result.returncode, flush=True)
    for line in (result.stdout+'\n'+result.stderr).splitlines():
        if 'result' in line or 'error:' in line or 'not found' in line:
            print(line[:4000],flush=True)

assert json.loads(Path('/workspace/profile-smoke.json').read_text())=={'ok':True}
print('Native local tool wrote and read valid JSON successfully.')

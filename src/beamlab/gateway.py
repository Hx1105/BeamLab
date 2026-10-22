"""Loopback MiMo relay. Real credentials are read only from protected env.

Request/response bodies are sanitized before archive; authentication headers
are never written. Response model labels are gateway evidence, not attestation.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from uuid import uuid4

MODEL = "mimo-v2.6-pro"
UPSTREAM = os.environ.get("MODEL_BASE_URL", "http://127.0.0.1:8317").rstrip("/")
DEVICE_TOOLS=('list_devices','read_device','call_device','set_device','stop_device','watch_device','unwatch_device')


def filter_tool_catalog(tools):
    selected=[t for t in tools if t.get('function',{}).get('name') in DEVICE_TOOLS]
    if {t['function']['name'] for t in selected}!=set(DEVICE_TOOLS):
        raise ValueError('Actual official seven-tool catalog is unavailable')
    return selected


def validate_request(payload):
    if payload.get("model") != MODEL:
        raise ValueError("BeamLab allows only mimo-v2.6-pro; no model fallback")


def redact_bytes(raw, secret):
    return raw.replace(secret.encode(), b"[REDACTED]") if secret else raw


def response_metadata(body):
    objects = []
    for line in body.decode("utf8", errors="replace").splitlines():
        if line.startswith("data: "):
            try:
                objects.append(json.loads(line[6:]))
            except ValueError:
                pass
    if not objects:
        try:
            objects.append(json.loads(body))
        except ValueError:
            pass
    objects = [x for x in objects if isinstance(x, dict)]
    return {"response_models": sorted({x["model"] for x in objects if isinstance(x.get("model"), str)}),
            "response_ids": sorted({x["id"] for x in objects if isinstance(x.get("id"), str)}),
            "usage": next((x["usage"] for x in reversed(objects) if x.get("usage")), None)}


def checked_sse_lines(lines):
    identified=False
    buffered=[]
    for line in lines:
        if line.startswith(b'data: '):
            payload=line[6:].strip()
            if payload!=b'[DONE]':
                obj=json.loads(payload)
                if 'model' in obj:
                    if obj['model']!=MODEL: raise ValueError('Response model identity mismatch; no fallback')
                    identified=True
            elif not identified:
                raise ValueError('Response model identity absent')
        if not identified:
            buffered.append(line)
        else:
            if buffered:
                yield from buffered;buffered=[]
            yield line
    if not identified:raise ValueError('Response model identity absent')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=44482)
    parser.add_argument("--evidence", required=True)
    parser.add_argument('--thinking-mode',choices=['enabled','disabled'],default='disabled')
    args = parser.parse_args()
    secret = os.environ.get("DEEPSEEK_API_KEY", "")
    if not secret:
        raise SystemExit("Existing protected user gateway credential is absent")
    out = Path(args.evidence)
    out.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def relay(self):
            if self.path not in ("/v1/chat/completions", "/v1/models"):
                self.send_error(404)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size > 8_000_000:
                self.send_error(413)
                return
            raw = self.rfile.read(size)
            payload = json.loads(raw) if raw else {}
            if self.command == "POST":
                original_raw=raw
                try:
                    validate_request(payload)
                except ValueError:
                    self.send_error(400, "Only mimo-v2.6-pro allowed")
                    return
                if payload.get("stream"):
                    payload["stream_options"] = {"include_usage": True}
                payload['thinking']={'type':args.thinking_mode}
                original_tool_count=len(payload.get('tools',[]))
                if payload.get('tools'):
                    payload['tools']=filter_tool_catalog(payload['tools'])
                raw = json.dumps(payload, ensure_ascii=False).encode()
            started, stamp = time.monotonic(), time.time()
            receipt = {"request_id": uuid4().hex, "started_unix": stamp,
                       "request_model": payload.get("model"), "channel": "protected user gateway / openai chat completions",
                       "upstream_origin": UPSTREAM, "credential_source": "inherited protected environment; value never archived",
                       "fallback_allowed": False, "identity_scope": "request and gateway response labels; not independent vendor weight attestation"}
            receipt['requested_thinking_mode']=args.thinking_mode
            receipt['relay_port']=args.port
            receipt['tool_presentation']={'scope':'Filter only; exact definitions supplied by official AGH @agnes/mhs',
                'original_count':locals().get('original_tool_count',0),'sent_count':len(payload.get('tools',[])),
                'names':[t.get('function',{}).get('name') for t in payload.get('tools',[])]}
            receipt['scope']='admission-probe' if self.headers.get('X-BeamLab-Probe')=='1' else 'session'
            receipt['mode_scope']='Explicit fixed MiMo mode, using documented thinking.type; never a different model'
            body=b''
            headers_sent=False
            try:
                request = urllib.request.Request(UPSTREAM+self.path, data=raw if self.command == "POST" else None,
                    headers={"Authorization": "Bearer "+secret, "Content-Type": "application/json"}, method=self.command)
                with opener.open(request, timeout=180) as response:
                    receipt["status"] = response.status
                    mime = response.headers.get("Content-Type", "application/json")
                    if payload.get('stream') and 'text/event-stream' in mime:
                        lines=checked_sse_lines(iter(response.readline,b''))
                        first=next(lines)
                        receipt['first_identity_s']=time.monotonic()-started
                        self.send_response(200)
                        self.send_header('Content-Type',mime)
                        self.send_header('Connection','close')
                        self.end_headers();headers_sent=True
                        body+=first;self.wfile.write(redact_bytes(first,secret));self.wfile.flush()
                        for line in lines:
                            body+=line
                            self.wfile.write(redact_bytes(line,secret));self.wfile.flush()
                    else:
                        body=response.read()
                        metadata=response_metadata(body)
                        if self.command=='POST' and metadata['response_models']!=[MODEL]:
                            raise ValueError('MiMo response identity missing or mismatch; no fallback')
                        safe_body=redact_bytes(body,secret)
                        self.send_response(200);self.send_header('Content-Type',mime)
                        self.send_header('Content-Length',str(len(safe_body)))
                        self.end_headers();headers_sent=True;self.wfile.write(safe_body)
                receipt['delivered_complete']=True
                self.close_connection=True
            except Exception as exc:
                receipt["error_type"] = type(exc).__name__
                receipt['delivered_complete']=False
                if not headers_sent:self.send_error(502, "Protected model channel unavailable; no fallback")
                self.close_connection=True
            finally:
                receipt.update(response_metadata(body))
                receipt["latency_s"] = time.monotonic()-started
                safe_request, safe_response = redact_bytes(raw, secret), redact_bytes(locals().get("body", b""), secret)
                receipt["request_sha256"] = hashlib.sha256(safe_request).hexdigest()
                receipt["response_sha256"] = hashlib.sha256(safe_response).hexdigest()
                if self.command == "POST":
                    folder = out/receipt["request_id"]
                    folder.mkdir()
                    (folder/"request.json").write_bytes(safe_request)
                    (folder/'agh-request.json').write_bytes(redact_bytes(original_raw,secret))
                    (folder/"response.txt").write_bytes(safe_response)
                    (folder/"receipt.json").write_text(json.dumps(receipt, indent=2))

        do_GET = relay
        do_POST = relay

    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(json.dumps({"relay_ready": True, "port": args.port, "model": MODEL}), flush=True)
        server.serve_forever(poll_interval=.2)


if __name__ == "__main__":
    main()

"""One prompt enters one native AGH session; no external decision loop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--run-id", required=True)
parser.add_argument("--prompt-file", required=True)
parser.add_argument("--timeout", type=int, default=600)
parser.add_argument('--follow-watch',action='store_true')
args = parser.parse_args()
if not args.run_id.replace("-", "").replace("_", "").isalnum():
    raise SystemExit("Invalid run ID")
out = ROOT/"runs"/args.run_id
out.mkdir(parents=True, exist_ok=False)
prompt = Path(args.prompt_file).read_text()
(out/"input.txt").write_text(prompt)
workspace = ROOT/"runtime/policy-workspaces"/uuid4().hex
workspace.mkdir(parents=True)
expected_session='agnes:local:local-dev:cli:workspace:'+hashlib.sha256(str(workspace).encode()).hexdigest()[:16]
(out/'session-route.json').write_text(json.dumps({'session_id':expected_session,'workspace':str(workspace)}))
cmd = [os.environ["BEAMLAB_NODE"], os.environ["BEAMLAB_AGH"], "-p", "--mode", "json", "--meta", "--cwd", str(workspace)]
env = os.environ.copy()
env.pop("DEEPSEEK_API_KEY", None)  # Only the loopback relay needs the real secret.
started = time.monotonic()
started_unix = time.time()
timed_out = False
with (out/"cli-events.jsonl").open("w") as stdout, (out/"stderr.log").open("w") as stderr:
    child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, env=env, cwd=ROOT, text=True)
    try:
        child.communicate(prompt, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
from beamlab.sessionlog import read_events, follow_complete, is_device_wakeup
if args.follow_watch and not timed_out and child.returncode==0:
    while time.monotonic()-started<args.timeout:
        if follow_complete(read_events(env['AGH_HOME'],expected_session)):
            break
        time.sleep(.25)
    else:timed_out=True
if timed_out:
    subprocess.run([env['BEAMLAB_NODE'],str(ROOT/'scripts/control-source.mjs'),'cancel',expected_session],
                   env=env,cwd=ROOT,stdout=(out/'deadline-cancel.json').open('w'),timeout=10,check=False)
    import asyncio
    from beamlab.hubclient import HubClient
    async def stop_after_deadline():
        async with HubClient(trace=out/'deadline-stop.jsonl') as hub:
            await hub.stop_all()
    asyncio.run(stop_after_deadline())
    # Wait briefly for the real cancel terminal event, not just notify ACK.
    until=time.monotonic()+5
    while time.monotonic()<until:
        current=read_events(env['AGH_HOME'],expected_session)
        terminals=[e for e in current if e['type'] in ('turn/start','turn/end')]
        if terminals and terminals[-1]['type']=='turn/end':break
        time.sleep(.1)
rows = []
for line in (out/"cli-events.jsonl").read_text().splitlines():
    try:
        rows.append(json.loads(line))
    except ValueError:
        pass
session = next((r["sessionId"] for r in reversed(rows) if r.get("sessionId")), expected_session)
events = []
if session:
    events=read_events(env['AGH_HOME'],session)
    import asyncio
    from beamlab.hubclient import HubClient
    async def clear_own_watches():
        async with HubClient(trace=out/'watch-teardown-hub.jsonl') as hub:
            return await hub.cancel_session_watches(events)
    (out/'watch-teardown.json').write_text(json.dumps(asyncio.run(clear_own_watches()),indent=2))
    (out/"agh-session.jsonl").write_text("".join(json.dumps(e,ensure_ascii=False)+"\n" for e in events))
last_end=next((e['data'] for e in reversed(events) if e['type']=='turn/end'),{})
follow_failed=args.follow_watch and last_end.get('reason')!='completed'
prompt_events=[e for e in events if e['type']=='user/message' and e['data'].get('kind')=='prompt']
result = {"session_id":session,"exit_code":child.returncode or int(follow_failed),"timed_out":timed_out,"wall_s":time.monotonic()-started,
          "started_unix":started_unix,"ended_unix":time.time(),
          "human_prompt_submissions":sum(not is_device_wakeup(e) for e in prompt_events),
          "device_auto_wakeups":sum(is_device_wakeup(e) for e in prompt_events),
          "external_step_prompts":0,"last_turn_end":last_end,
          "command":cmd,"cli_result":rows[-1] if rows else None,
          "event_count":len(events),"followed_device_watch":args.follow_watch,
          "turn_count":sum(e['type']=='turn/start' for e in events),
          "requested_model":"mimo-v2.6-pro","model_identity_requires_actual_receipts":True}
(out/"invocation.json").write_text(json.dumps(result,indent=2,ensure_ascii=False))
print(json.dumps(result,ensure_ascii=False))
raise SystemExit(child.returncode or int(timed_out) or int(follow_failed))

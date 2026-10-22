"""Independent launcher/judge: one initial prompt, no per-step guidance."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from beamlab.evaluation import judge
from beamlab.hubclient import HubClient

ROOT=Path(__file__).resolve().parents[1]
PYTHON=str(ROOT/'.venv/bin/python')

def active(value):
    if os.environ.get('BEAMLAB_INSTANCE_ACTIVE') or os.environ.get('BEAMLAB_BACKGROUND')!='1':
        Path(os.environ.get('BEAMLAB_INSTANCE_ACTIVE',str(ROOT/'runtime/active-run.json'))).write_text(json.dumps(value))


def read_json(path):return json.loads(Path(path).read_text())


def llm_report(folder):
    events=[json.loads(s) for s in (folder/'agh-session.jsonl').read_text().splitlines()]
    messages=[''.join(b.get('text','') for b in e['data'].get('content',[]) if b.get('type')=='text')
              for e in events if e['type']=='assistant/message']
    parsed=None
    for message in messages:
        for match in re.finditer(r'\{',message):
            try:
                r,_=json.JSONDecoder().raw_decode(message[match.start():])
                if isinstance(r,dict) and 'diagnosis' in r:parsed=r
            except ValueError:pass
    text='\n'.join(messages)
    result=parsed or {'diagnosis':'uncertain','reported_recovered':False,'maintenance':False,'error':'No structured final diagnosis'}
    result['full_report']=text
    result['tool_calls']=sum(e['type']=='tool/call' for e in events)
    result['approval_events']=[e for e in events if e['type'].startswith('approval/')]
    results=[e['data'] for e in events if e['type']=='tool/result']
    result['tool_rejections']=sum(bool(r.get('isError')) for r in results)
    result['clamped_requests']=sum('clamp' in json.dumps(r.get('structured',{}).get('notes',[])).lower() for r in results)
    return result




async def one(case,policy,prefix='development',timeout=600,prompt_file='protocol/task-input.txt',demonstration=False):
    run_id=f'{prefix}-{case["id"]}-{policy}'
    out=ROOT/'runs'/run_id
    out.mkdir(exist_ok=False)
    physical=out/'physical'
    process_log=(out/'device-server.log').open('w')
    env=os.environ.copy();env.pop('DEEPSEEK_API_KEY',None)
    lab=subprocess.Popen([PYTHON,'-m','beamlab.lab','--trace',str(physical),'--category',case['fault_class'],
                          '--seed',str(case['physics_seed'])],cwd=ROOT,env=env,stdout=process_log,stderr=subprocess.STDOUT)
    started=time.monotonic();started_unix=time.time()
    active({'run_id':run_id,'policy':policy,'started_unix':started_unix,'physical':str(physical),'state':'running'})
    failure=None;report={};invocation=None;operator=None
    try:
        async with asyncio.timeout(15):
            while not (physical/'ready.json').exists():
                if lab.poll() is not None:raise RuntimeError('Device server ended before readiness')
                await asyncio.sleep(.1)
        if policy=='mimo':
            session_run=run_id+'-agh'
            runner=[PYTHON,'scripts/run_agh.py','--run-id',session_run,'--prompt-file',prompt_file,'--timeout',str(timeout)]
            if demonstration:
                runner.append('--follow-watch')
                raise ValueError("Use the standard task mode")
            child=await asyncio.create_subprocess_exec(*runner,cwd=ROOT,env=env,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
            raw,_=await child.communicate()
            (out/'runner.log').write_bytes(raw)
            folder=ROOT/'runs'/session_run
            invocation=read_json(folder/'invocation.json')
            if (folder/'agh-session.jsonl').exists():report=llm_report(folder)
            if child.returncode:failure=f'AGH ended with code {child.returncode}'
            report['session_run']=session_run
            report['invocation']=invocation
        else:
            from beamlab.baselines import Policy
            async with HubClient(trace=out/'policy-hub.jsonl') as hub:
                async with asyncio.timeout(timeout):
                    report=await Policy(hub,policy,out/'policy-report.json').run()
        oracle=read_json(physical/'evaluator/case.json')['oracle']
        judged=judge(physical,oracle,json.dumps(report,ensure_ascii=False))
        # Use actual gateway receipts in this session's wall-time interval.
        receipts=[]
        if policy=='mimo':
            for file in (ROOT/'evidence/model-requests').glob('*/receipt.json'):
                r=read_json(file)
                if (r.get('request_model')=='mimo-v2.6-pro' and r.get('scope','session')=='session'
                    and r.get('relay_port',44482)==int(os.environ.get('BEAMLAB_RELAY_PORT','44482'))
                    and invocation['started_unix']<=r['started_unix']<=invocation['ended_unix']):
                    receipts.append({**r,'archive':str(file.relative_to(ROOT))})
        (out/'model-receipts.json').write_text(json.dumps(receipts,indent=2))
        usage={'input':0,'output':0,'cached_input':0}
        for r in receipts:
            u=r.get('usage') or {};usage['input']+=u.get('prompt_tokens',0);usage['output']+=u.get('completion_tokens',0)
            usage['cached_input']+=(u.get('prompt_tokens_details') or {}).get('cached_tokens',0)
        result={**case,'policy':policy,'run_id':run_id,'wall_s':time.monotonic()-started,
                'judgment':judged,'report':report,'failure':failure,'model_calls':len(receipts),'tokens':usage,
                'monetary_cost':None,'cost_reason':'No verified gateway billing; AGH zero estimates are not actual cost'}
        result['usage_missing_calls']=sum(not r.get('usage') for r in receipts)
        result['incomplete_model_calls']=sum(not r.get('delivered_complete',False) for r in receipts)
        result['usage_scope']='Sum of available actual usage receipts; missing usage is not estimated as zero cost'
        result['task_completed']=bool((judged['recovered'] or judged['maintenance_correct']) and not failure
            and (policy!='mimo' or report.get('diagnosis')!='uncertain'))
        (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
        active({'run_id':run_id,'policy':policy,'state':'finished'})
        print(json.dumps({'case':case['id'],'policy':policy,'recovered':judged['recovered'],
              'maintenance':judged['maintenance_correct'],'measurements':judged['target_measurements'],
              'wall_s':round(result['wall_s'],2),'failure':failure}),flush=True)
        return result
    finally:
        if operator and not operator.done():
            operator.cancel()
            await asyncio.gather(operator,return_exceptions=True)
        try:
            async with HubClient(trace=out/'end-stop.jsonl') as h:await h.stop_all()
        except Exception as exc:(out/'end-stop-error.txt').write_text(type(exc).__name__)
        lab.send_signal(signal.SIGTERM)
        try:await asyncio.to_thread(lab.wait,10)
        except subprocess.TimeoutExpired:lab.kill();lab.wait()
        process_log.close()


async def main(args):
    import fcntl
    namespace=os.environ.get('BEAMLAB_LOCK_NAMESPACE','baseline' if os.environ.get('BEAMLAB_BACKGROUND')=='1' else 'bench')
    if not namespace.replace('-','').isalnum():raise ValueError('Invalid isolated bench namespace')
    with (ROOT/'runtime'/(namespace+'.lock')).open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|(0 if args.wait_lock else fcntl.LOCK_NB))
        await run_cases(args)

async def run_cases(args):
    scenarios=read_json(ROOT/'experiments/scenarios.json')['cases']
    cases=[c for c in scenarios if not args.case or c['id'] in args.case]
    if not cases:raise ValueError('Unknown scenario')
    for case in cases:
        for policy in args.policy or ['mimo']:
            await one(case,policy,args.prefix,args.timeout,args.prompt_file,False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',action='append')
    p.add_argument('--policy',action='append');p.add_argument('--prefix',required=True)
    p.add_argument('--timeout',type=int,default=600)
    p.add_argument('--prompt-file',default='protocol/task-input.txt')
    p.add_argument('--wait-lock',action='store_true')
    asyncio.run(main(p.parse_args()))

"""Local audience/control UI. The AGH tool boundary cannot access this HTTP UI."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from uuid import uuid4
import zipfile
from .replay import curve_row, field_png
from .replay_state import state_timeline

ROOT=Path(__file__).resolve().parents[2]
WEB=ROOT/'web'
LOCK=threading.Lock()
JOB=None
ACTIVE=Path(os.environ.get('BEAMLAB_INSTANCE_ACTIVE',str(ROOT/'runtime/active-run.json')))


def read_json(path, default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default


def selected_run(run_id=None):
    if run_id:
        if not run_id.replace('-','').replace('_','').isalnum():raise ValueError('Invalid run ID')
        return run_id
    active=read_json(ACTIVE,{})
    if active.get('run_id'):return active['run_id']
    runs=sorted((ROOT/'runs').glob('*/result.json'),key=lambda p:p.stat().st_mtime)
    return runs[-1].parent.name if runs else None


def session_events(run_id):
    folder=ROOT/'runs'/(run_id+'-agh')
    route=read_json(folder/'session-route.json',{})
    invocation=read_json(folder/'invocation.json',{})
    session=route.get('session_id') or invocation.get('session_id')
    if not session:return []
    db=Path(os.environ.get('AGH_HOME',str(ROOT/'runtime/agh-home')))/'data/sessions.db'
    try:
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as c:
            from datetime import datetime
            rows=[{'seq':seq,'type':kind,'t':datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp(),
                     'data':json.loads(data)} for seq,kind,ts,data in
                    c.execute("select seq,type,ts,data from events where session_key=? and type in ('tool/call','tool/result','assistant/message','turn/end','request/sent') order by seq",(session,))]
            if rows:return rows
    except sqlite3.Error:pass
    archive=folder/'agh-session-final.jsonl'
    if not archive.exists():archive=folder/'agh-session.jsonl'
    rows=[json.loads(l) for l in archive.read_text().splitlines()] if archive.exists() else []
    from datetime import datetime
    for row in rows:
        if 't' not in row:row['t']=datetime.fromisoformat(row['ts'].replace('Z','+00:00')).timestamp()
    return rows


def state(run_id=None):
    run_id=selected_run(run_id)
    if not run_id:return {'ready':False,'mode':'LIVE','message':'尚无实验，请提交任务或选择实际回放。'}
    folder=ROOT/'runs'/run_id;physical=folder/'physical'
    public=read_json(physical/'public-state.json',{})
    result=read_json(folder/'result.json')
    report=(result or {}).get('report',{})
    samples=[]
    try:
        for line in (physical/'measurements.jsonl').read_text().splitlines():
            try:samples.append(json.loads(line))
            except ValueError:pass
    except OSError:pass
    events=session_events(run_id)
    calibration={'target':report.get('target_dark_w'),'reference':report.get('reference_dark_w')}
    # During a live session derive calibration only from actual dark samples.
    dark=[s for s in samples if not s['shutter_open']]
    if calibration['target'] is None and dark:
        calibration['target']=sum(s['target_w'] for s in dark)/len(dark)
        calibration['reference']=sum(s['reference_w'] for s in dark)/len(dark)
    curves=[curve_row(s,calibration['target'] or 0,calibration['reference'] or 0) for s in samples]
    audience={}
    try:
        for line in (physical/'evaluator/physical-trace.jsonl').read_text().splitlines():
            try:
                r=json.loads(line)
                audience[r['sample']]={key:r[key] for key in ('effective_optical_angles_deg','receiver_centroid_mm') if key in r}
            except ValueError:pass
    except OSError:pass
    for row in curves:row['audience_geometry']=audience.get(row['sample'],{})
    for i in range(2):
        actual=read_json(physical/f'actual/mirror-0{i+1}-actual.json')
        if actual and 'angles_deg' in public:
            public['angles_deg'][2*i:2*i+2]=[actual['x_deg'],actual['y_deg']]
    return {'ready':True,'run_id':run_id,'mode':'RECORDED' if result else 'LIVE',
            'started_unix':report.get('invocation',{}).get('started_unix',samples[0]['t'] if samples else None),
            'finished_unix':report.get('invocation',{}).get('ended_unix',samples[-1]['t'] if samples else None),
            'public':public,'state_timeline':state_timeline(physical),'curve':curves,'events':events,'result':result,'calibration':calibration,
            'agent_inputs':'标量测量 + 编码器 + 环境；图像仅供观众查看','simulated':True,
            'field_samples':[int(p.stem) for p in (physical/'evaluator/fields').glob('*.npz')]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def json_response(self,value,status=200):
        self.bytes_response(json.dumps(value,ensure_ascii=False).encode(),'application/json',status)
    def bytes_response(self,raw,mime,status=200):
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        from urllib.parse import urlsplit,parse_qs
        url=urlsplit(self.path);query=parse_qs(url.query)
        try:
            if url.path=='/api/state':return self.json_response(state(query.get('run',[None])[0]))
            if url.path=='/api/runs':
                runs=[]
                for p in sorted((ROOT/'runs').glob('*/result.json'),key=lambda p:p.stat().st_mtime,reverse=True):
                    r=read_json(p,{});runs.append({'run_id':p.parent.name,'policy':r.get('policy'),'case':r.get('id'),
                        'recovered':r.get('judgment',{}).get('recovered'),'measurements':r.get('judgment',{}).get('target_measurements')})
                return self.json_response(runs)
            if url.path=='/api/field':
                run_id=selected_run(query.get('run',[None])[0]);sample=int(query.get('sample',['0'])[0])
                folder=ROOT/'runs'/run_id/'physical/evaluator'
                field=folder/'fields'/f'{sample:03d}.npz'
                if not field.exists():field=folder/'latest-field.npz'
                if not field.exists():return self.json_response({'error':'尚未采集复光场'},404)
                cache=ROOT/'runtime/field-cache';cache.mkdir(exist_ok=True)
                png=cache/f'{run_id}-{sample}.png'
                if not png.exists():
                    import numpy as np
                    with np.load(field) as f:field_png(f['field'],png,float(f['dx_m']))
                return self.bytes_response(png.read_bytes(),'image/png')
            if url.path=='/api/summary':return self.json_response(read_json(ROOT/'deliverables/evaluation-summary.json',{'formal_started':False}))
            if url.path=='/api/download':
                run_id=selected_run(query.get('run',[None])[0]);folder=ROOT/'runs'/run_id
                out=ROOT/'deliverables/replays';out.mkdir(exist_ok=True)
                target=out/f'{run_id}.zip'
                with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
                    for p in folder.rglob('*'):
                        if p.is_file():z.write(p,str(p.relative_to(folder)))
                    session=ROOT/'runs'/(run_id+'-agh')
                    if session.exists():
                        for p in session.rglob('*'):
                            if p.is_file():z.write(p,'agh-session/'+str(p.relative_to(session)))
                    receipts=read_json(folder/'model-receipts.json',[])
                    invocation=read_json(session/'invocation.json',{})
                    start=invocation.get('started_unix');end=invocation.get('ended_unix')
                    ports={r.get('relay_port',44482) for r in receipts}
                    if start and end and len(ports)==1:
                        port=next(iter(ports))
                        # Include genuine receipts that finished after result.json.
                        receipts=[]
                        for p in (ROOT/'evidence/model-requests').glob('*/receipt.json'):
                            r=read_json(p,{})
                            if r.get('relay_port',44482)==port and r.get('scope','session')=='session' and start<=r.get('started_unix',0)<=end:
                                receipts.append({**r,'archive':str(p.relative_to(ROOT))})
                    for receipt in receipts:
                        relative=receipt.get('archive')
                        if not relative:continue
                        archive=(ROOT/relative).resolve().parent
                        if not archive.is_relative_to((ROOT/'evidence/model-requests').resolve()):continue
                        for p in archive.iterdir():
                            if p.is_file():z.write(p,'model-channel/'+archive.name+'/'+p.name)
                    raw_sources=[]
                    if start and end:
                        for wire in (ROOT/'evidence').glob('*-wire/wire.jsonl'):
                            lines=[]
                            for line in wire.open():
                                try:row=json.loads(line)
                                except ValueError:continue
                                if start-15<=row.get('observed_t',0)<=end+3:lines.append(line)
                            if lines:
                                name='raw-protocol/'+wire.parent.name+'.jsonl'
                                z.writestr(name,''.join(lines));raw_sources.append(name)
                    z.writestr('bundle-scope.json',json.dumps({'run_id':run_id,
                        'simulated_devices':True,'raw_protocol_time_slices':raw_sources,
                        'time_slice_note':'Timestamp slices may include other independent benches; device endpoint and raw source are preserved',
                        'full_model_archives':len(receipts),'physics':'CPU paraxial scalar coherent wave optics',
                        'agent_had_camera_or_hidden_fault_access':False},indent=2))
                self.send_response(200);self.send_header('Content-Type','application/zip')
                self.send_header('Content-Disposition',f'attachment; filename="{run_id}.zip"')
                self.end_headers();self.wfile.write(target.read_bytes());return
            file=(WEB/(url.path.lstrip('/') or 'index.html')).resolve()
            if not file.is_relative_to(WEB.resolve()) or not file.is_file():return self.json_response({'error':'未找到'},404)
            self.bytes_response(file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        except (ValueError,OSError) as exc:self.json_response({'error':type(exc).__name__},400)
    def do_POST(self):
        global JOB
        size=int(self.headers.get('Content-Length','0'))
        if size>16000:return self.json_response({'error':'任务过长'},413)
        try:body=json.loads(self.rfile.read(size))
        except ValueError:return self.json_response({'error':'任务格式无效'},400)
        if self.path=='/api/task':
            if os.environ.get('BEAMLAB_REPLAY_ONLY')=='1':
                return self.json_response({'error':'当前明确为离线回放模式；实时任务需启动受保护 MiMo 渠道。'},503)
            with LOCK:
                active=read_json(ACTIVE,{})
                if (JOB and JOB.poll() is None) or active.get('state')=='running':
                    return self.json_response({'error':'装置正在执行实验，请等待当前会话结束。'},409)
                task=body.get('task','').strip()
                if not task:return self.json_response({'error':'请输入自然语言任务'},400)
                choices={'demonstration_alignment':'development-geometric-2','demonstration':'development-source_drift-1',
                         'geometric':'development-geometric-2','source_drift':'development-source_drift-1',
                         'thermal':'development-thermal-1','combined':'development-combined-1',
                         'mechanical_stuck':'maintenance-1'}
                case=choices.get(body.get('scenario','geometric'),'development-geometric-2')
                prefix='live-'+uuid4().hex[:10]
                request=ROOT/'runtime/requests';request.mkdir(exist_ok=True)
                path=request/(prefix+'.txt')
                constraints=(ROOT/'protocol/task-input.txt').read_text()
                alignment=body.get('scenario')=='demonstration_alignment'
                demo=alignment or body.get('scenario')=='demonstration'
                if demo:constraints+='\n'+(ROOT/('protocol/demo-alignment-extension.txt' if alignment else 'protocol/demo-extension.txt')).read_text()
                path.write_text(task+'\n\n装置任务边界与报告约定：\n'+constraints)
                env=os.environ.copy();env.pop('DEEPSEEK_API_KEY',None)
                log=(request/(prefix+'.log')).open('w')
                split='maintenance' if case.startswith('maintenance') else 'development'
                command=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/experiment.py'),
                    '--prefix',prefix,'--split',split,'--case',case,'--policy','mimo','--timeout','900',
                    '--prompt-file',str(path)]
                if demo:
                    command[command.index('--timeout')+1]='1800'
                    command.append('--demonstration')
                if alignment:command=[str(ROOT/'.venv/bin/python'),'scripts/demo_alignment_experiment.py','--prefix',prefix,'--prompt-file',str(path),'--timeout','1800']
                JOB=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                return self.json_response({'started':True,'run_id':f'{prefix}-{case}-mimo','human_task_inputs':1},202)
        if self.path=='/api/stop':
            active=read_json(ACTIVE,{})
            run_id=active.get('run_id')
            if run_id:
                route=read_json(ROOT/'runs'/(run_id+'-agh')/'session-route.json',{})
                if route.get('session_id'):
                    subprocess.run([os.environ['BEAMLAB_NODE'],str(ROOT/'scripts/control.mjs'),'cancel',route['session_id']],
                                   stdout=subprocess.DEVNULL,timeout=10,check=False)
            import asyncio
            from .hubclient import HubClient
            async def stop():
                async with HubClient(trace=ROOT/'runtime/ui-stop.jsonl') as h:await h.stop_all()
            asyncio.run(stop())
            return self.json_response({'stopped':True,'scope':'BeamLab session and simulated devices only'})
        self.json_response({'error':'未找到'},404)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=44484);args=p.parse_args()
    print(json.dumps({'ui_ready':True,'url':f'http://127.0.0.1:{args.port}','simulated':True}),flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

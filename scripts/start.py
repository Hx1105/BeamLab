"""Portable owned-instance startup. Credentials stay in inherited relay memory."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
PY=str(ROOT/'.venv/bin/python')
NODE=str(ROOT/'vendor/node')
CLI=str(ROOT/'vendor/agh-runtime/agnes.mjs')


def env_for(base, instance=''):
    env=os.environ.copy()
    home=ROOT/'runtime'/instance
    env.update(BEAMLAB_ROOT=str(ROOT),AGH_HOME=str(home/'agh-home'),AGNES_PROFILE='local-dev',
        AGNES_HUB_LISTEN=f'127.0.0.1:{base}',AGNES_HUB_ORIGIN=f'http://127.0.0.1:{base}',
        AGNES_HUB_DATA=str(home/'hub-data'),BEAMLAB_HUB_AGENT=f'ws://127.0.0.1:{base}/ws/hub',
        BEAMLAB_DEVICE_URL=f'ws://127.0.0.1:{base+1}',BEAMLAB_NODE=NODE,BEAMLAB_AGH=CLI,
        BEAMLAB_UPSTREAM=os.environ.get('AGH_SOURCE_DIR') or json.loads((ROOT/'runtime/agent-dependencies.json').read_text())['source'],AGNES_SECRET_BEAMLAB_LOCAL='beamlab-local-placeholder',
        PYTHONPATH=str(ROOT/'src'),
        BEAMLAB_RELAY_PORT=str(base+2),BEAMLAB_LOCK_NAMESPACE=instance or 'bench',
        BEAMLAB_INSTANCE_ACTIVE=str(home/'active-run.json'),
        OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',MKL_NUM_THREADS='4')
    return env


def identity(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
    except OSError:return None


def ready(port, timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.2):return
        except OSError:time.sleep(.1)
    raise RuntimeError('Owned service did not become ready on port '+str(port))


def main(args):
    if args.instance and not args.instance.replace('-','').isalnum():raise SystemExit('Invalid instance name')
    runtime=ROOT/'runtime'/args.instance;runtime.mkdir(parents=True,exist_ok=True)
    record=runtime/'services.json'
    base=args.base_port;env=env_for(base,args.instance);safe_env=env.copy();safe_env.pop('DEEPSEEK_API_KEY',None)
    if args.action=='stop':
        if not record.exists():raise SystemExit('No startup-owned service record; will not touch unrelated processes')
        services=json.loads(record.read_text())
        for p in reversed(services['processes']):
            if identity(p['pid'])==p['start_identity']:os.kill(p['pid'],signal.SIGTERM)
        subprocess.run([NODE,CLI,'daemon','stop'],cwd=ROOT,env=safe_env,check=False)
        record.unlink();print(json.dumps({'stopped':True,'scope':str(ROOT)}));return
    if record.exists():raise SystemExit('Startup record exists. Inspect status or use its owned stop before restarting.')
    ports=[base+4] if args.action=='replay' else [base,base+1,base+2,base+4]
    for port in ports:
        with socket.socket() as s:
            try:s.bind(('127.0.0.1',port))
            except OSError:raise SystemExit('Port occupied; no process stopped: '+str(port))
    if args.action=='start' and not env.get('DEEPSEEK_API_KEY'):
        raise SystemExit('Existing protected MiMo gateway credential absent; no model fallback')
    # Fail before creating a profile or daemon if the locked Python runtime is absent.
    subprocess.run([PY,'-c','import websockets, numpy, scipy, LightPipes, jsonschema'],env=safe_env,check=True)
    processes=[]
    daemon_started=False
    logs=runtime/'service-logs';logs.mkdir(exist_ok=True)
    def launch(name,cmd,child_env):
        file=(logs/(name+'.log')).open('a')
        p=subprocess.Popen(cmd,cwd=ROOT,env=child_env,stdout=file,stderr=subprocess.STDOUT,start_new_session=True)
        file.close();processes.append({'name':name,'pid':p.pid,'start_identity':identity(p.pid)})
    try:
        if args.action=='start':
            profile=Path(env['AGH_HOME'])/'profiles/local-dev/profile.yaml'
            profile.parent.mkdir(parents=True,exist_ok=True)
            if profile.exists():raise RuntimeError('Existing profile found; inspect rather than overwrite')
            profile.write_text((ROOT/'protocol/agh-profile.yaml').read_text().replace('127.0.0.1:44482',f'127.0.0.1:{base+2}'))
            launch('model-relay',[PY,'-m','beamlab.gateway','--port',str(base+2),
                   '--evidence','evidence/model-requests','--thinking-mode','disabled'],env)
            ready(base+2)
            subprocess.run([NODE,CLI,'daemon','start'],cwd=ROOT,env=safe_env,check=True,
                stdout=(logs/'daemon-start.log').open('a'),stderr=subprocess.STDOUT,timeout=60)
            daemon_started=True
            # Runtime-only port descriptor; pinned plugin source remains intact.
            import shutil
            plugin=runtime/'port-configured-mhs'
            shutil.copytree(ROOT/'plugins/mhs',plugin,dirs_exist_ok=False)
            desc=plugin/'client/agnes.client.json'
            desc.write_text(desc.read_text().replace('127.0.0.1:44480',f'127.0.0.1:{base}'))
            for id,ref in [('@agnes/mhs','file:./'+str(plugin.relative_to(ROOT))),('@beamlab/agent-boundary','file:./plugins/guard')]:
                subprocess.run([NODE,str(ROOT/'scripts/control-source.mjs'),'activate',id,ref],cwd=ROOT,env=safe_env,
                    check=True,timeout=90,stdout=(logs/'plugin-activation.log').open('a'),stderr=subprocess.STDOUT)
            ready(base)
            launch('wire-recorder',[PY,'-m','beamlab.tracehub','--port',str(base+1),
                '--upstream',f'ws://127.0.0.1:{base}','--trace','evidence/'+(args.instance+'-wire' if args.instance else 'live-wire'),
                '--control',str(runtime/'trace-control')],safe_env)
            ready(base+1)
        else:safe_env['BEAMLAB_REPLAY_ONLY']='1'
        launch('audience-ui',[PY,'-m','beamlab.webserver','--port',str(base+4)],safe_env)
        ready(base+4)
        record.write_text(json.dumps({'root':str(ROOT),'base_port':base,'mode':args.action,'processes':processes},indent=2))
        public_names=('BEAMLAB_ROOT','AGH_HOME','AGNES_PROFILE','AGNES_HUB_LISTEN','AGNES_HUB_ORIGIN',
            'AGNES_HUB_DATA','BEAMLAB_HUB_AGENT','BEAMLAB_DEVICE_URL','BEAMLAB_NODE','BEAMLAB_AGH',
            'BEAMLAB_UPSTREAM','BEAMLAB_RELAY_PORT','BEAMLAB_LOCK_NAMESPACE','BEAMLAB_INSTANCE_ACTIVE','PYTHONPATH',
            'OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS')
        public_env={k:safe_env[k] for k in public_names}
        (runtime/'instance-environment.json').write_text(json.dumps(public_env,indent=2))
        print(json.dumps({'started':True,'mode':args.action,'url':f'http://127.0.0.1:{base+4}',
                          'model':'mimo-v2.6-pro' if args.action=='start' else None,'fallback':False}))
    except Exception as exc:
        for p in reversed(processes):
            if identity(p['pid'])==p['start_identity']:os.kill(p['pid'],signal.SIGTERM)
        if daemon_started:
            subprocess.run([NODE,CLI,'daemon','stop'],cwd=ROOT,env=safe_env,check=False,timeout=30,
                stdout=(logs/'failed-start-owned-stop.log').open('a'),stderr=subprocess.STDOUT)
        (runtime/'startup-failure.json').write_text(json.dumps({'error':str(exc),
            'owned_processes':processes,'owned_daemon_stop_requested':daemon_started},indent=2))
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','replay','stop'])
    p.add_argument('--base-port',type=int,default=44480);p.add_argument('--instance',default='');main(p.parse_args())

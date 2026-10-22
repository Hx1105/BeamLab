"""Serve six independent official Python Devices sharing one simulated field."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
from .world import World
from .evaluation import inject_case, reachable_coupling
from .hubclient import HubClient


async def serve_lab(args):
    out=Path(args.trace);out.mkdir(parents=True,exist_ok=True)
    if (out/'evaluator/case.json').exists():
        raise RuntimeError('Existing experiment trace will not be overwritten')
    w=World(noise=not args.no_noise,noise_seed=args.seed,trace_dir=out,n=args.grid)
    injected=inject_case(w,args.category,args.seed)
    oracle=reachable_coupling(w)
    (out/'evaluator/case.json').write_text(json.dumps({**injected,'oracle':oracle},indent=2))
    devices=w.make_devices()
    quit_event=asyncio.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig,quit_event.set)
    tasks=[asyncio.create_task(d.run(args.hub)) for d in devices]
    tasks += [asyncio.create_task(m.stream()) for m in w.mirrors]
    tasks.append(asyncio.create_task(w.stream_environment()))
    tasks.append(asyncio.create_task(w.stream_instruments()))
    async def independent_control():
        command_path=out/'evaluator/operator-command.json'
        ack_path=out/'evaluator/operator-ack.json'
        while not quit_event.is_set():
            if command_path.exists() and not ack_path.exists():
                try:
                    command=json.loads(command_path.read_text())
                    # A disconnected device cannot be driven by old commands.
                    if any(d._cmd is None for d in devices):
                        raise RuntimeError('Independent command rejected: a device is disconnected')
                    reply=await w.operator_perturb(command)
                except Exception as exc:reply={'applied':False,'error':str(exc),'retried':False}
                ack_path.write_text(json.dumps(reply))
            await asyncio.sleep(.1)
    tasks.append(asyncio.create_task(independent_control()))
    try:
        async with HubClient(trace=out/'observer-hub.jsonl') as observer:
            await observer.ready()
            await observer.record_sources()
            (out/'ready.json').write_text(json.dumps({'ready':True,'simulated_devices':6,'grid':args.grid}))
            print(json.dumps({'world_ready':True,'devices':[d.id for d in devices]}),flush=True)
            await quit_event.wait()
            await observer.stop_all()
    finally:
        w.stop_mirrors();w.set_shutter(False)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--hub',default=os.environ.get('BEAMLAB_DEVICE_URL','ws://127.0.0.1:44481'))
    parser.add_argument('--trace',required=True)
    parser.add_argument('--category',default='combined')
    parser.add_argument('--seed',type=int,default=19501)
    parser.add_argument('--grid',type=int,default=256)
    parser.add_argument('--no-noise',action='store_true')
    asyncio.run(serve_lab(parser.parse_args()))

if __name__=='__main__': main()

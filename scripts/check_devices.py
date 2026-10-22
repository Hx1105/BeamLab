"""Unmodified official mhs-check, isolated test port, every applicable profile."""
import asyncio
from collections import Counter
import json
from pathlib import Path
from websockets.asyncio.server import serve
from agnes_mhs.check import CheckHub, run, no_operator
from beamlab.world import World

ROOT=Path(__file__).resolve().parents[1]


async def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',default='evidence/conformance-v2');p.add_argument('--device',type=int);p.add_argument('--port',type=int,default=44488)
    args=p.parse_args()
    output=ROOT/args.output;output.mkdir(exist_ok=True)
    for i in (range(6) if args.device is None else [args.device]):
        w=World(noise=False,trace_dir=output/f'device-{i}-physical')
        devices=w.make_devices();device=devices[i]
        hub=CheckHub(motion=True,ask=no_operator)
        tasks=[]
        async with serve(hub.handler,'127.0.0.1',args.port,subprotocols=['mhs.v1'],ping_interval=2,ping_timeout=3):
            tasks.append(asyncio.create_task(device.run(f'ws://127.0.0.1:{args.port}')))
            if i in (1,2):tasks.append(asyncio.create_task(w.mirrors[i-1].stream()))
            if i==5:tasks.append(asyncio.create_task(w.stream_environment()))
            if i==3:tasks.append(asyncio.create_task(w.stream_instruments()))
            try:
                await run(hub,10,2,True,lambda line:None,None)
            except Exception as exc:
                hub.report.add('CONN-6','fail',type(exc).__name__+': '+str(exc))
            finally:
                w.stop_mirrors();w.set_shutter(False)
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        results=hub.report.final(hub.profiles)
        report={'device':device.id,'tool':'official mhs-check 1.0.0','protocol':'MHS/MOS 1.0',
                'profiles':hub.profiles,'results':results,'counts':dict(Counter(r['status'] for r in results)),
                'physical_checks':'Manual checker items remain manual; independent field/controller checks are separate'}
        (output/f'{device.id}.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'device':device.id,'counts':report['counts']}),flush=True)

asyncio.run(main())

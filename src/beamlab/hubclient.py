"""Small real AgnesHub client, shared by checkers and every non-LLM policy."""
import asyncio
import json
import os
from pathlib import Path
import time
from websockets.asyncio.client import connect


class HubClient:
    def __init__(self, url=None, trace=None):
        self.url=url or os.environ.get('BEAMLAB_HUB_AGENT','ws://127.0.0.1:44480/ws/hub')
        self.trace=Path(trace) if trace else None
        self.pending={}
        self.results={}
        self.events=[]
        self.counter=0
        self.ws=None
        self.reader=None

    def record(self, direction, row):
        if self.trace:
            self.trace.parent.mkdir(parents=True,exist_ok=True)
            with self.trace.open('a') as f:
                f.write(json.dumps({'t':time.time(),'direction':direction,'message':row})+'\n')

    async def __aenter__(self):
        self.ws=await connect(self.url)
        self.reader=asyncio.create_task(self.read_loop())
        await self.rpc('hub/hello',{'role':'ui','client':{'name':'beamlab-recorder-or-policy','version':'0.1.0'}})
        return self

    async def __aexit__(self, *args):
        await self.ws.close()
        self.reader.cancel()
        await asyncio.gather(self.reader,return_exceptions=True)

    async def read_loop(self):
        try:
            async for raw in self.ws:
                if isinstance(raw,bytes):
                    continue
                row=json.loads(raw)
                self.record('hub_to_client',row)
                if 'id' in row and row['id'] in self.pending:
                    future=self.pending.pop(row['id'])
                    if not future.done(): future.set_result(row)
                elif row.get('method')=='hub/result':
                    p=row['params'];self.results[p['job']]=p
                else:
                    self.events.append(row)
        finally:
            for f in self.pending.values():
                if not f.done(): f.set_exception(ConnectionError('Hub connection closed; outcome unknown'))

    async def rpc(self, method, params=None, timeout=8):
        self.counter+=1; rid=str(self.counter)
        row={'jsonrpc':'2.0','id':rid,'method':method,'params':params or {}}
        future=asyncio.get_running_loop().create_future();self.pending[rid]=future
        self.record('client_to_hub',row)
        await self.ws.send(json.dumps(row))
        try:
            reply=await asyncio.wait_for(future,timeout)
        finally:
            self.pending.pop(rid,None)
        if 'error' in reply: raise RuntimeError(json.dumps(reply['error']))
        return reply['result']

    async def call(self, device, tool, arguments=None, timeout=35):
        accepted=await self.rpc('hub/call',{'device':device,'tool':tool,'arguments':arguments or {}})
        if not accepted.get('accepted'):
            raise RuntimeError('Device refusal: '+json.dumps(accepted))
        job=accepted['job']
        try:
            async with asyncio.timeout(timeout):
                while job not in self.results: await asyncio.sleep(.01)
            return self.results.pop(job)
        except TimeoutError:
            # Read actual state and known outcome, never resend an unknown action.
            await self.rpc('hub/read',{'device':device})
            known=await self.rpc('hub/job',{'job':job})
            if known.get('result'): return known['result']
            await self.rpc('hub/stop',{'device':device})
            raise TimeoutError('Outcome unknown; read state, queried job and stopped locally')

    async def stop_all(self):
        # Stop domain executor first; the source closes before any next action.
        for device in ('target-01','source-01','mirror-01','mirror-02','environment-01'):
            try: await self.rpc('hub/stop',{'device':device},timeout=3)
            except (RuntimeError,TimeoutError,ConnectionError): pass

    async def cancel_session_watches(self, events):
        ids={e['data']['toolUseId'] for e in events if e['type']=='tool/call' and e['data']['name']=='watch_device'}
        watches={e['data'].get('structured',{}).get('watch') for e in events if e['type']=='tool/result'
                 and e['data']['toolUseId'] in ids and not e['data'].get('isError')}
        results=[]
        for watch in sorted(w for w in watches if w):
            try:
                reply=await self.rpc('hub/unwatch',{'watch':watch})
                results.append({'watch':watch,'removed':True,'reply':reply})
            except RuntimeError as exc:
                results.append({'watch':watch,'removed':False,'actual_reply':str(exc)})
        return results

    async def ready(self, timeout=10):
        async with asyncio.timeout(timeout):
            while True:
                row=await self.rpc('hub/devices')
                if len([d for d in row['devices'] if d.get('available')])>=6: return row
                await asyncio.sleep(.1)

    async def record_sources(self):
        for device,sources in [('target-01',['samples','instrument_status']),('reference-01',['samples']),
                               ('mirror-01',['encoder_precise','axes']),('mirror-02',['encoder_precise','axes']),
                               ('environment-01',['telemetry'])]:
            await self.rpc('hub/subscribe',{'device':device,'sources':sources,
                'hz':20 if device in ('target-01','reference-01') else 10})

"""Policies see only real Hub replies. No evaluator, field or injection access.

All four main baselines share a strong measured diagnostic preflight. The
diagnostic_fit baseline calls the identical local numerical device tool as AGH.
The two ablations change only diagnosis or normalization, respectively.
"""
import asyncio
import json
from pathlib import Path
import time
import numpy as np
from scipy.optimize import minimize
from .hubclient import HubClient

POLICIES=('fixed_coarse_fine','coordinate','powell','diagnostic_fit',
          'ablation_no_normalization','ablation_no_diagnosis')


class BudgetEnd(Exception): pass


class Policy:
    def __init__(self, hub, name, output):
        self.hub=hub;self.name=name;self.output=Path(output)
        self.count=0;self.dark_t=5e-6;self.dark_r=1e-6
        self.positions=np.zeros(4);self.flags=[];self.best=None
        self.actions=[];self.diagnosis='uncertain'

    async def call(self, device, tool, args=None):
        r=await self.hub.call(device,tool,args)
        self.actions.append({'device':device,'tool':tool,'arguments':args or {},'reply':r})
        if r['status']!='done': raise RuntimeError(json.dumps(r))
        return r.get('data',{})

    async def sample(self, cap=40):
        if self.count>=cap: raise BudgetEnd()
        s=await self.call('target-01','measure',{'acquisition_id':f'target-{self.count+1}'})
        self.count=s['sample'];return s

    def ratio(self,s):
        return (s['target_w']-self.dark_t)/max(1e-9,s['reference_w']-self.dark_r)

    async def move(self,x):
        x=np.clip(np.asarray(x),-.08,.08)
        for i in range(2):
            r=await self.call(f'mirror-0{i+1}','move',{'x_deg':float(x[2*i]),'y_deg':float(x[2*i+1])})
            self.positions[2*i:2*i+2]=[r['x_deg'],r['y_deg']]

    async def preflight(self):
        # Actual reference, dark and range experiments precede any optics.
        await self.hub.ready()
        for device in ('source-01','mirror-01','mirror-02','target-01','reference-01','environment-01'):
            await self.hub.rpc('hub/read',{'device':device})
        ref=await self.call('reference-01','measure',{'acquisition_id':'preflight-reference'})
        initial=await self.sample()
        await self.call('source-01','shutter',{'open':False})
        dark=[await self.sample(),await self.sample()]
        self.dark_t=float(np.mean([s['target_w'] for s in dark]))
        self.dark_r=float(np.mean([s['reference_w'] for s in dark]))
        if self.dark_t>10e-6 or self.dark_r>2e-6: self.flags.append('dark_bias')
        if abs((ref['reference_w']-self.dark_r)/50e-6-1)>.20: self.flags.append('source_drift')
        await self.call('source-01','shutter',{'open':True})
        if initial['range_w']<.002:
            await self.hub.rpc('hub/set',{'device':'target-01','values':{'range_w':.002}})
        light=await self.sample()
        # Saturated initial and high-range untruncated readings distinguish clipping.
        if initial['saturated'] and not light['saturated']: self.flags.append('saturation')
        env=await self.call('environment-01','observe')
        if abs(env['temperature_c']-22)>1.:
            self.flags.append('thermal')
            await self.call('environment-01','condition',{'setpoint_c':22.})
            light=await self.sample()
        if self.ratio(light)<17.1: self.flags.append('geometric')
        # Thermal shifts are removed by temperature; remaining coupling loss
        # is an independently observed geometric component.
        self.diagnosis='combined' if len(self.flags)>1 else (self.flags[0] if self.flags else 'uncertain')
        return light

    async def fixed_scan(self):
        for radius in (.04,.008):
            for axis in range(4):
                if self.count+3>37: return
                c=self.positions[axis]
                result=await self.call('target-01','scan_axis',{'axis':axis,
                    'low_deg':float(max(-.08,c-radius)),'high_deg':float(min(.08,c+radius)),
                    'points':3,'target_dark_w':self.dark_t,'reference_dark_w':self.dark_r})
                self.positions[:]=result['position_deg'];self.count+=3

    async def coordinate(self):
        current=await self.sample(cap=37);value=self.ratio(current)
        step=.025
        while self.count+8<=37:
            improved=False
            for axis in range(4):
                center=self.positions.copy();best=(value,center.copy())
                for direction in (-1,1):
                    trial=center.copy();trial[axis]=np.clip(center[axis]+direction*step,-.08,.08)
                    await self.move(trial);s=await self.sample(cap=37);v=self.ratio(s)
                    if v>best[0]:best=(v,trial.copy());improved=True
                value=best[0];await self.move(best[1])
            step*=.6 if improved else .4

    async def powell(self):
        loop=asyncio.get_running_loop()
        best=[-float('inf'),self.positions.copy()]
        async def evaluate(x):
            await self.move(x);s=await self.sample(cap=37)
            if s['saturated']: raise ValueError('Saturated objective')
            v=self.ratio(s)
            if v>best[0]:best[:]=[v,self.positions.copy()]
            return -v
        def objective(x):
            return asyncio.run_coroutine_threadsafe(evaluate(x),loop).result(timeout=10)
        def run():
            try:
                return minimize(objective,self.positions.copy(),method='Powell',bounds=[(-.08,.08)]*4,
                                options={'maxfev':max(1,37-self.count),'xtol':.0005,'ftol':.001})
            except BudgetEnd: return None
        await asyncio.to_thread(run)
        await self.move(best[1])

    async def run(self):
        started=time.monotonic();error=None;maintenance=False
        try:
            if self.name=='ablation_no_diagnosis':
                await self.hub.ready();light=await self.sample()
            else:
                light=await self.preflight()
            need_optics=self.ratio(light)<17.3 or self.name=='ablation_no_diagnosis'
            if need_optics:
                if self.name=='fixed_coarse_fine':await self.fixed_scan()
                elif self.name=='coordinate':await self.coordinate()
                elif self.name=='powell':await self.powell()
                else:
                    if self.ratio(light)<1. and self.name!='ablation_no_diagnosis':
                        # Two measured coarse axes lift a lost beam above noise;
                        # the same scan tool is available to every policy.
                        for axis in (0,1):
                            r=await self.call('target-01','scan_axis',{'axis':axis,'low_deg':-.06,'high_deg':.06,
                                'points':5,'target_dark_w':self.dark_t,'reference_dark_w':self.dark_r})
                            self.positions[:]=r['position_deg'];self.count+=5
                    result=await self.call('target-01','fit_local',{'radius_deg':.05,
                        'target_dark_w':self.dark_t,'reference_dark_w':self.dark_r,'max_samples':18,
                        'normalize':self.name!='ablation_no_normalization'})
                    self.count+=result['samples_used'];self.positions[:]=result['position_deg']
            final=[await self.sample() for _ in range(3)]
            reported=all(not s['saturated'] and self.ratio(s)>=17.1 for s in final)
        except Exception as exc:
            error=str(exc);reported=False
            if 'stuck' in error.lower() or 'maintenance' in error.lower():
                self.diagnosis='mechanical_stuck';maintenance=True
            # Unknown result is not retried; read actual state, then stop.
            actual=await self.hub.rpc('hub/read',{'device':'target-01'})
            # A failed scan/fit can have acquired before returning an error.
            # The independently recorded physical trace remains authoritative.
            self.count=actual.get('state',{}).get('values',{}).get('measurements',self.count)
            await self.hub.stop_all()
        result={'policy':self.name,'diagnosis':self.diagnosis,'findings':self.flags,
                'target_dark_w':self.dark_t,'reference_dark_w':self.dark_r,
                'reported_recovered':reported,'maintenance':maintenance,'error':error,
                'measurements':self.count,'wall_s':time.monotonic()-started,'model_calls':0,
                'tool_rejections':sum(a['reply'].get('status')!='done' for a in self.actions),
                'clamped_requests':sum('clamp' in json.dumps(a['reply'].get('notes',[])).lower() for a in self.actions),
                'actions':self.actions}
        self.output.parent.mkdir(parents=True,exist_ok=True)
        self.output.write_text(json.dumps(result,indent=2))
        return result


async def main_async(args):
    async with HubClient(trace=Path(args.output).with_suffix('.hub.jsonl')) as hub:
        return await Policy(hub,args.policy,args.output).run()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--policy',choices=POLICIES,required=True)
    parser.add_argument('--output',required=True)
    print(json.dumps(asyncio.run(main_async(parser.parse_args())),ensure_ascii=False))

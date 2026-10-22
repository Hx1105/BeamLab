"""Audience state timeline from actual Hub and controller traces, no future state."""
import json
from pathlib import Path

def state_timeline(physical):
 physical=Path(physical);rows=[]
 observer=physical/'observer-hub.jsonl'
 if observer.exists():
  for line in observer.open():
   try:r=json.loads(line)
   except ValueError:continue
   m=r.get('message',{});p=m.get('params',{})
   if m.get('method')!='hub/state':continue
   d=p.get('device');v=p.get('values',{});patch={}
   if d=='source-01' and 'open' in v:patch['shutter_open']=v['open']
   if d=='environment-01':
    if 'temperature_c' in v:patch['temperature_c']=v['temperature_c']
    if 'interlock_ok' in v:patch['interlock_ok']=v['interlock_ok']
   if d=='target-01':
    for k,new in [('measurements','target_measurements'),('remaining','budget_remaining')]:
     if k in v:patch[new]=v[k]
   if patch:rows.append({'t':p.get('time',r['t']),'patch':patch})
 motion=physical/'actual/physical-motion.jsonl'
 if motion.exists():
  for line in motion.open():
   try:r=json.loads(line)
   except ValueError:continue
   d=r.get('device')
   if d not in ['mirror-01','mirror-02']:continue
   rows.append({'t':r['t'],'mirror':int(d[-1])-1,'angles_deg':[r['x_deg'],r['y_deg']],'moving':r['moving']})
 return sorted(rows,key=lambda r:r['t'])

def state_at(timeline,t):
 p={};angles=[0.,0.,0.,0.]
 for r in timeline:
  if r['t']>t:break
  p.update(r.get('patch',{}))
  if 'mirror' in r:
   i=r['mirror']*2;angles[i:i+2]=r['angles_deg'];p['angles_deg']=angles.copy()
 return p

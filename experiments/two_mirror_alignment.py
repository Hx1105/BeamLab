"""Align two mirrors through measured power feedback."""
from pathlib import Path
import sys,asyncio,json,numpy as np
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from beamlab.world import World
from beamlab.optimizer import optimize_local
async def main():
 w=World(noise=False);w.hidden_offsets_deg[:]=[.027,-.018,-.022,.014]
 before=w.optical_result().coupling
 async def move(a):
  for i,m in enumerate(w.mirrors):await m.move_to(*a[2*i:2*i+2])
 async def sample():return w.acquire()
 r=await optimize_local(move,sample,center=np.zeros(4),radius_deg=.045,target_dark_w=5e-6,reference_dark_w=1e-6,max_samples=18)
 print(json.dumps({'coupling_before':before,'coupling_after':w.optical_result().coupling,'samples':r['samples_used']},indent=2))
asyncio.run(main())

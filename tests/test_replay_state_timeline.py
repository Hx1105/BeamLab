"""A replay must show a real shutter transition before the next acquisition."""
import json
from beamlab.replay_state import state_timeline, state_at

def test_inter_acquisition_shutter_and_partial_motion_are_replayed_from_receipts(tmp_path):
 observer=tmp_path/'observer-hub.jsonl'
 observer.write_text('\n'.join(json.dumps({'t':t,'message':{'method':'hub/state','params':{'device':d,'time':t,'values':v}}}) for t,d,v in [(10,'source-01',{'open':False}),(12,'source-01',{'open':True}),(12,'environment-01',{'temperature_c':22.4})]))
 actual=tmp_path/'actual';actual.mkdir();(actual/'physical-motion.jsonl').write_text('\n'.join(json.dumps({'t':t,'device':'mirror-01','x_deg':x,'y_deg':0,'moving':m}) for t,x,m in [(10,0,False),(12.1,.01,True),(12.2,.02,False)]))
 timeline=state_timeline(tmp_path)
 assert state_at(timeline,11)['shutter_open'] is False
 p=state_at(timeline,12.15)
 assert p['shutter_open'] is True and p['angles_deg'][0]==.01
 assert p['temperature_c']==22.4
 assert state_at(timeline,12.3)['angles_deg'][0]==.02
 assert 'shutter_open' not in state_at(timeline,9)

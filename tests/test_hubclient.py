import importlib.util
import asyncio
import pytest


def test_hub_client_unknown_outcome_does_not_replay_action():
    assert importlib.util.find_spec('beamlab.hubclient'), 'Shared protocol client absent'
    from beamlab.hubclient import HubClient
    class Fake(HubClient):
        def __init__(self):
            super().__init__()
            self.calls=[]
        async def rpc(self, method, params=None, timeout=8):
            self.calls.append(method)
            if method=='hub/call': return {'accepted':True,'job':'j1'}
            if method=='hub/job': return {'result':{'status':'done','data':{'x':1}}}
            return {}
    async def run():
        c=Fake()
        result=await c.call('mirror-01','move',{'x_deg':0,'y_deg':0},timeout=.01)
        assert result['status']=='done'
        assert c.calls==['hub/call','hub/read','hub/job']
    asyncio.run(run())

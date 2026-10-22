import asyncio
import json
import time
import pytest
from beamlab.world import World


def command():
    return dict(id='independent-perturb-1', expires_unix=time.time()+3,
                offset_delta_deg=[.014,-.012,-.015,.011])


def test_operator_perturbation_is_private_and_one_real_budgeted_acquisition(tmp_path):
    w=World(noise=False,trace_dir=tmp_path)
    assert hasattr(w,'operator_perturb'), 'Independent operator intervention absent'
    before=w.optical_result().coupling
    reply=asyncio.run(w.operator_perturb(command()))
    assert reply['applied'] is True
    assert w.target_measurements==1
    assert w.optical_result().coupling<before
    assert 'offset_delta' not in json.dumps(w.public_snapshot())
    assert w.last_sample['acquisition_id']=='operator:independent-perturb-1'
    assert (tmp_path/'evaluator/operator-injections.jsonl').exists()


def test_operator_rejects_expired_or_busy_without_old_action_resuming():
    w=World(noise=False)
    assert hasattr(w,'operator_perturb')
    c=command();c['expires_unix']=time.time()-1
    with pytest.raises(Exception,match='expired'):asyncio.run(w.operator_perturb(c))
    w.active_operation='fit_local'
    with pytest.raises(Exception,match='busy'):asyncio.run(w.operator_perturb(command()))
    assert w.target_measurements==0

import importlib.util
import numpy as np
from beamlab.world import World


def test_hidden_case_injection_is_deterministic_and_not_in_public_observations():
    assert importlib.util.find_spec("beamlab.evaluation"), "Independent case evaluator has not been implemented"
    from beamlab.evaluation import inject_case, reachable_coupling
    a,b=World(noise=False),World(noise=False)
    inject_case(a,"combined",19001)
    inject_case(b,"combined",19001)
    assert np.array_equal(a.hidden_offsets_deg,b.hidden_offsets_deg)
    assert a.source_multiplier==b.source_multiplier
    assert reachable_coupling(a)["coupling"]>.99
    assert a.optical_result().coupling < .9
    assert "hidden_offsets_deg" not in a.public_snapshot()

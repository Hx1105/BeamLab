import asyncio
import importlib.util
import numpy as np
from beamlab.world import World


def test_local_quadratic_optimizer_uses_only_measured_callback_and_finite_budget():
    assert importlib.util.find_spec("beamlab.optimizer"), "Local numerical fitter has not been implemented"
    from beamlab.optimizer import optimize_local
    w = World(noise=False)
    w.hidden_offsets_deg[:] = [.027, -.018, -.022, .014]
    async def move(angles):
        for i, m in enumerate(w.mirrors):
            await m.move_to(*angles[2*i:2*i+2])
    async def sample():
        return w.acquire()
    before = w.optical_result().coupling
    result = asyncio.run(optimize_local(move, sample, center=np.zeros(4), radius_deg=.045,
                                       target_dark_w=5e-6,reference_dark_w=1e-6,max_samples=18))
    assert w.target_measurements <= 18
    assert result["samples_used"] == w.target_measurements
    assert before < .9
    assert w.optical_result().coupling > .90


def test_local_optimizer_rejects_uncalibrated_saturated_samples():
    assert importlib.util.find_spec("beamlab.optimizer"), "Local numerical fitter has not been implemented"
    from beamlab.optimizer import optimize_local
    async def move(_):
        pass
    async def sample():
        return {"target_w":.0002,"reference_w":.000051,"saturated":True}
    import pytest
    with pytest.raises(Exception, match="saturat"):
        asyncio.run(optimize_local(move, sample, center=np.zeros(4),radius_deg=.02,
                                  target_dark_w=0,reference_dark_w=0,max_samples=18))


def test_noisy_development_offset_fit_does_not_probe_below_noise_floor():
    from beamlab.optimizer import optimize_local
    from beamlab.evaluation import inject_case
    w=World(noise=True)
    inject_case(w,'geometric',19002)
    async def move(p):await w.move_angles(p)
    async def sample():return w.acquire()
    asyncio.run(optimize_local(move,sample,center=np.zeros(4),radius_deg=.05,
        target_dark_w=5e-6,reference_dark_w=1e-6,max_samples=18))
    assert w.optical_result().coupling>.9


def test_public_gaussian_curvature_handles_different_acquisition_noise_indices():
    from beamlab.optimizer import optimize_local
    from beamlab.evaluation import inject_case
    w=World(noise=True,n=256)
    inject_case(w,'geometric',19002)
    # Actual observed failed development run: three darks, two light samples.
    w.target_measurements=5
    async def move(p):await w.move_angles(p)
    async def sample():return w.acquire()
    result=asyncio.run(optimize_local(move,sample,center=np.zeros(4),radius_deg=.05,
        target_dark_w=4.654e-6,reference_dark_w=.929e-6,max_samples=18))
    assert w.optical_result().coupling>.9
    assert result['samples_used']<=18


def test_public_gaussian_prior_matches_independent_unapertured_wave_propagation():
    from beamlab.optimizer import public_gaussian_curvature
    from beamlab.physics import OpticalBench
    import pytest
    b=OpticalBench(256)
    angles=np.array([.014,-.012,-.015,.011])
    predicted=np.exp(-.5*angles@public_gaussian_curvature(1.)@angles)
    assert b.propagate(angles,aperture=False).coupling==pytest.approx(predicted,rel=2e-7)

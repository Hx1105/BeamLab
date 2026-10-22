"""Independent optical invariants, not visual or optimizer self-assessment."""
import importlib.util
import math
import numpy as np
import pytest


def engine(n=128, window_m=0.010):
    assert importlib.util.find_spec("beamlab.physics"), "CPU wave optics engine has not been implemented"
    from beamlab.physics import OpticalBench
    return OpticalBench(n=n, window_m=window_m)


def test_free_propagation_conserves_integral_and_gaussian_radius():
    b = engine(256)
    field = b.gaussian_free(1.0)
    power, cx, cy, radius = b.moments(field)
    expected = b.waist_m * math.sqrt(1 + (b.wavelength_m / (math.pi * b.waist_m**2))**2)
    assert power == pytest.approx(1.0, abs=2e-12)
    assert radius == pytest.approx(expected, rel=2e-4)
    assert abs(cx) + abs(cy) < 1e-8


def test_mechanical_degrees_produce_twice_the_reflected_radian_angle():
    b = engine(256)
    theta_deg = math.degrees(0.25e-3)
    field = b.reflection_probe(theta_deg, z_m=1.0)
    power, cx, cy, radius = b.moments(field)
    assert power == pytest.approx(1, abs=2e-12)
    assert cx == pytest.approx(0.5e-3, rel=1e-3)
    free = b.gaussian_free(1.0)
    eta = b.overlap(field, free)
    assert eta == pytest.approx(math.exp(-(2*math.pi/b.wavelength_m*b.waist_m*0.5e-3)**2/4), abs=1e-7)


def test_two_mirror_path_power_peak_and_grid_window_convergence():
    samples = []
    for n, window in [(128, .010), (256, .010), (512, .010), (256, .014)]:
        b = engine(n, window)
        aligned = b.propagate([0, 0, 0, 0])
        offset = b.propagate([.018, -.013, -.012, .009])
        assert b.propagate([.018, -.013, -.012, .009], aperture=False).integral == pytest.approx(1, abs=3e-12)
        assert aligned.coupling > .995
        assert offset.coupling < aligned.coupling - .08
        assert 0 <= offset.coupling <= offset.integral <= 1 + 1e-12
        assert offset.boundary_fraction < 1e-5
        samples.append(offset.coupling)
    assert max(samples) - min(samples) < .002


def test_power_scaling_does_not_change_coupling():
    b = engine()
    low = b.propagate([.014, .002, -.005, -.012], source_power_w=.0003)
    high = b.propagate([.014, .002, -.005, -.012], source_power_w=.001)
    assert high.coupling == pytest.approx(low.coupling, abs=1e-12)
    assert high.received_power_w/low.received_power_w == pytest.approx(10/3)

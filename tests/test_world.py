import asyncio
import importlib.util
import json
import pytest


def world(**kwargs):
    assert importlib.util.find_spec("beamlab.world"), "Multi-device optical world has not been implemented"
    from beamlab.world import World
    return World(**kwargs)


def test_target_dark_reference_and_source_strength_are_separate():
    w = world(noise=False)
    w.set_shutter(False)
    dark = w.acquire()
    w.set_shutter(True)
    first = w.acquire()
    w.source_multiplier = .4
    second = w.acquire()
    r1 = (first["target_w"]-dark["target_w"])/(first["reference_w"]-dark["reference_w"])
    r2 = (second["target_w"]-dark["target_w"])/(second["reference_w"]-dark["reference_w"])
    assert r2 == pytest.approx(r1, rel=1e-12)
    assert second["target_w"]-dark["target_w"] == pytest.approx(.4*(first["target_w"]-dark["target_w"]))
    assert dark["target_w"] == w.target_dark_w


def test_detector_saturation_and_range_are_actual_measurements():
    w = world(noise=False)
    w.range_w = .0002
    low = w.acquire()
    assert low["saturated"] is True and low["target_w"] == .0002
    w.range_w = .002
    high = w.acquire()
    assert high["saturated"] is False and high["target_w"] > low["target_w"]


def test_budget_counts_dark_and_optimizer_samples_and_never_exceeds_40():
    w = world(noise=False)
    for _ in range(40):
        w.acquire()
    with pytest.raises(Exception, match="budget"):
        w.acquire()
    assert w.target_measurements == 40


def test_measurement_interlock_blocks_moving_mirrors_without_spending_budget():
    w = world(noise=False)
    w.mirrors[0].moving = True
    with pytest.raises(Exception, match="moving"):
        w.acquire()
    assert w.target_measurements == 0


def test_agent_snapshot_has_no_injected_category_offsets_or_optimum():
    w = world(noise=False)
    w.hidden_offsets_deg[:] = [.025, -.015, .01, .02]
    text = json.dumps(w.public_snapshot())
    for forbidden in ["hidden_offsets", "oracle", "fault_class", "reachable", "seed", ".025"]:
        assert forbidden not in text


def test_all_five_classes_register_as_six_real_devices_with_valid_schemas():
    w = world(noise=False)
    from agnes_mhs import Device, errors
    devices = w.make_devices()
    assert len(devices) == 6
    assert len({d.id for d in devices}) == 6
    for dev in devices:
        assert isinstance(dev, Device)
        assert errors("RegisterParams", dev.description()) == []
    detector = next(d for d in devices if d.id == "target-01")
    names = [t["name"] for t in detector.description()["tools"]]
    assert "measure" in names and "fit_local" in names and "scan_axis" in names
    assert not any(name in names for name in ["solve_task", "run_all", "align_successfully"])


def test_domain_stop_prevents_optimizer_from_resuming_next_mirror_move():
    w=world(noise=False)
    w.make_devices()
    w.active_operation='fit_local'
    w.domain_epoch=0
    w.stop_mirrors()
    assert w.domain_epoch>0, 'Domain stop needs a cancellation epoch'


def test_source_local_stop_updates_state_as_well_as_actual_shutter():
    w=world(noise=False)
    source=w.make_devices()[0]
    source.on_stop()
    assert w.shutter_open is False
    assert source._state['open'] is False


def test_duplicate_acquisition_identity_rejects_replay_without_extra_sample():
    w=world(noise=False)
    w.claim_acquisition('independent-1')
    w.acquire()
    with pytest.raises(Exception,match='replay'):
        w.claim_acquisition('independent-1')
    assert w.target_measurements==1

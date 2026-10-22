import asyncio
import importlib.util
import json
import pytest


def make():
    assert importlib.util.find_spec("beamlab.devices"), "MHS mirror adapter has not been implemented"
    from beamlab.devices import Mirror
    return Mirror("mirror-test", move_duration_s=.8)


def test_mirror_declares_motion_with_limits_and_real_sdk():
    mirror = make()
    from agnes_mhs import Device, is_valid
    assert isinstance(mirror.device, Device)
    description = mirror.device.description()
    assert is_valid("RegisterParams", description)
    tool = next(t for t in description["tools"] if t["name"] == "move")
    assert tool["motion"] is True
    assert tool["uses"] == ["axes"]
    assert tool["inputSchema"]["properties"]["x_deg"]["maximum"] == .08


def test_encoder_reports_sub_centidegree_values_without_rounding():
    mirror = make()
    mirror.position[:] = [.000123456, -.015]
    assert json.loads(mirror.precise_encoder())["y_deg"] == -.015
    assert any(s["id"] == "encoder_precise" and s["kind"] == "text" for s in mirror.device.description()["sources"])


def test_local_stop_ends_physical_motion_and_no_automatic_resume():
    mirror = make()
    async def run():
        task = asyncio.create_task(mirror.move_to(.06, -.04))
        await asyncio.sleep(.15)
        mirror.stop_local()
        stopped_at = mirror.position.copy()
        await task
        await asyncio.sleep(.12)
        assert mirror.position == stopped_at
        assert mirror.moving is False
        assert stopped_at != [.06, -.04]
    asyncio.run(run())

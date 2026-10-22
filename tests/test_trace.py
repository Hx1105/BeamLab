import importlib.util
import json


def test_raw_wire_recorder_preserves_message_and_direction(tmp_path):
    assert importlib.util.find_spec("beamlab.tracehub"), "Raw MHS/MOS recorder has not been implemented"
    from beamlab.tracehub import Recorder
    r = Recorder(tmp_path)
    raw = '{"type":"data","source":"axes","seq":9,"t":123.5,"data":{"x_deg":0.012}}'
    r.record("mirror-01", "nerve", "device_to_hub", raw)
    envelope = json.loads((tmp_path/"wire.jsonl").read_text())
    assert envelope["raw"] == raw
    assert envelope["direction"] == "device_to_hub"
    assert json.loads((tmp_path/"mirror-01-messages.jsonl").read_text())["seq"] == 9

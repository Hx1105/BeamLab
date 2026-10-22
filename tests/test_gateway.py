import importlib.util
import pytest


def module():
    assert importlib.util.find_spec("beamlab.gateway"), "MiMo audit relay has not been implemented"
    from beamlab import gateway
    return gateway


def test_relay_refuses_any_model_fallback():
    g = module()
    assert g.validate_request({"model": "mimo-v2.6-pro", "messages": []}) is None
    with pytest.raises(ValueError, match="mimo-v2.6-pro"):
        g.validate_request({"model": "another-model"})


def test_real_stream_receipt_aggregates_identity_and_usage():
    g = module()
    body = b'data: {"id":"r1","model":"mimo-v2.6-pro","choices":[{"delta":{"content":"ok"}}]}\n\ndata: {"id":"r1","model":"mimo-v2.6-pro","usage":{"prompt_tokens":20,"completion_tokens":5}}\n\ndata: [DONE]\n'
    receipt = g.response_metadata(body)
    assert receipt["response_models"] == ["mimo-v2.6-pro"]
    assert receipt["usage"]["prompt_tokens"] == 20
    assert receipt["response_ids"] == ["r1"]


def test_archive_redacts_credential_even_if_upstream_echoes_it():
    g = module()
    assert b"secret-example" not in g.redact_bytes(b'{"error":"secret-example"}', "secret-example")

import pytest
from beamlab import gateway


def test_stream_checks_identity_before_first_byte_and_never_falls_back():
    assert hasattr(gateway,'checked_sse_lines'), 'Validated streaming relay absent'
    good=[b'data: {"model":"mimo-v2.6-pro","choices":[]}\n',b'\n',b'data: [DONE]\n']
    assert list(gateway.checked_sse_lines(iter(good)))==good
    with pytest.raises(ValueError,match='identity'):
        list(gateway.checked_sse_lines(iter([b'data: {"model":"other"}\n'])))
    with pytest.raises(ValueError,match='identity'):
        list(gateway.checked_sse_lines(iter([b'data: [DONE]\n'])))

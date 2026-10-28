import pytest
from cartio_core.protocol import (
    CheckIn,
    CheckInResponse,
    EnrollRequest,
    EnrollResponse,
    ProtocolError,
)


def test_messages_round_trip():
    message = CheckIn(hostname="vm01", version="0.1.0", platform="Linux", tls_inspected=True)
    assert CheckIn.from_dict(message.to_dict()) == message
    assert CheckInResponse.from_dict({"status": "ok", "jobs": []}).jobs == []
    enrolled = EnrollResponse(collector_id="c1", customer="Acme", tls_pins=["p"])
    assert EnrollResponse.from_dict(enrolled.to_dict()) == enrolled


def test_missing_or_wrong_type_field_is_rejected():
    with pytest.raises(ProtocolError, match="missing field: hostname"):
        EnrollRequest.from_dict({"token": "t"})
    with pytest.raises(ProtocolError, match="must be str"):
        EnrollRequest.from_dict({"token": 1, "hostname": "vm01", "public_key": "k"})
    with pytest.raises(ProtocolError, match="must be bool"):
        CheckIn.from_dict({"hostname": "h", "version": "1", "platform": "p", "tls_inspected": 1})
    with pytest.raises(ProtocolError, match="list of strings"):
        EnrollResponse.from_dict({"collector_id": "c", "customer": "A", "tls_pins": [1]})

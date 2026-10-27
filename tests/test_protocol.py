import pytest
from cartio_core.protocol import (
    CheckIn,
    CheckInResponse,
    EnrollRequest,
    ProtocolError,
    bearer,
    parse_bearer,
)


def test_messages_round_trip():
    message = CheckIn(hostname="vm01", version="0.1.0", platform="Linux")
    assert CheckIn.from_dict(message.to_dict()) == message
    assert CheckInResponse.from_dict({"status": "ok", "jobs": []}).jobs == []


def test_missing_or_wrong_type_field_is_rejected():
    with pytest.raises(ProtocolError, match="missing field: hostname"):
        EnrollRequest.from_dict({"token": "t"})
    with pytest.raises(ProtocolError, match="must be str"):
        EnrollRequest.from_dict({"token": 1, "hostname": "vm01"})


def test_bearer_round_trip():
    assert parse_bearer(bearer("abc", "s3cret")) == ("abc", "s3cret")


@pytest.mark.parametrize("header", ["", "Basic abc.def", "Bearer abc", "Bearer .def"])
def test_bad_bearer_is_rejected(header):
    with pytest.raises(ProtocolError):
        parse_bearer(header)

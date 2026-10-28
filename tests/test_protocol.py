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
    assert CheckInResponse.from_dict({"status": "ok"}).status == "ok"
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


def test_job_messages_round_trip_and_check_their_ranges():
    from cartio_core.protocol import Job, JobPoll, JobPollResponse, JobProgress, JobResult

    answer = JobPollResponse(jobs=[Job(id="j1", type="health", engagement=3)])
    assert JobPollResponse.from_dict(answer.to_dict()) == answer
    result = JobResult(result_id="r1", outcome="partial", chunks=2, sha256="ab", device_count=4)
    assert JobResult.from_dict(result.to_dict()) == result
    assert JobProgress.from_dict({"message": "Hop 1", "percent": 20}).percent == 20
    for bad in (
        lambda: JobPoll.from_dict({"wait": 26, "tls_inspected": False}),
        lambda: JobPoll.from_dict({"wait": True, "tls_inspected": False}),
        lambda: JobProgress.from_dict({"message": "x", "percent": 101}),
        lambda: JobResult.from_dict({**result.to_dict(), "outcome": "cancelled"}),
        lambda: JobResult.from_dict({**result.to_dict(), "chunks": -1}),
        lambda: Job.from_dict({**Job(id="j", type="health").to_dict(), "engagement": "3"}),
    ):
        with pytest.raises(ProtocolError):
            bad()

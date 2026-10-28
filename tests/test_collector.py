"""The collector on its own, against a stand-in controller."""

import json
import os
import sys

import httpx
import pytest
from cartio_collector import cli, client
from cartio_collector.client import ControllerError, Session, check_in, enroll
from cartio_collector.config import CollectorConfig
from cartio_core import signing

CONFIG = CollectorConfig(
    controller_url="https://controller.example",
    collector_id="00000000-0000-0000-0000-000000000001",
    customer="Acme",
    private_key=signing.new_private_key(),
)
CHECKED_IN = {"status": "ok", "jobs": []}


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "url",
    ["http://controller.example", "http://10.0.0.5:8000", "ftp://controller.example"],
)
def test_refuses_plain_http_to_another_machine(url):
    called = []
    with pytest.raises(ControllerError, match="https"):
        enroll(url, "token", http=_http(lambda r: called.append(r)))
    assert called == []


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]"])
def test_allows_plain_http_to_this_machine(url):
    answer = {"collector_id": "c1", "customer": "Acme", "tls_pins": []}
    config = enroll(url, "token", http=_http(lambda r: httpx.Response(201, json=answer)))
    assert config.collector_id == "c1"


def test_enroll_sends_only_the_public_key_and_keeps_the_private_one():
    sent = []

    def controller(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={"collector_id": "c1", "customer": "A", "tls_pins": ["p"]})

    config = enroll("https://controller.example", "token", http=_http(controller))
    assert sent[0]["public_key"] == signing.public_key_for(config.private_key)
    assert config.private_key not in json.dumps(sent[0])
    assert config.tls_pins == ["p"]


def test_every_request_after_enrolling_is_signed():
    seen = []

    def controller(request):
        seen.append(request)
        return httpx.Response(200, json=CHECKED_IN)

    check_in(CONFIG, http=_http(controller))
    request = seen[0]
    signed = signing.parse(request.headers["Authorization"])
    assert signed.collector_id == CONFIG.collector_id
    signing.verify(
        signing.public_key_for(CONFIG.private_key),
        signed,
        "POST",
        "/api/v1/checkin",
        request.content,
    )
    assert json.loads(request.content)["tls_inspected"] is False


def test_a_key_that_isnt_pinned_is_reported_as_inspection(monkeypatch):
    config = CollectorConfig(**{**CONFIG.__dict__, "tls_pins": ["controller-pin"]})
    reports = []

    def controller(request):
        reports.append(json.loads(request.content)["tls_inspected"])
        return httpx.Response(200, json=CHECKED_IN)

    monkeypatch.setattr(client, "server_pin", lambda response: "proxy-pin")
    with Session(config, http=_http(controller)) as session:
        session.check_in()
        # The first report couldn't know yet, so it is corrected at once; then it stays right.
        assert reports == [False, True]
        session.check_in()
        assert reports == [False, True, True]
        assert session.inspected

    monkeypatch.setattr(client, "server_pin", lambda response: "controller-pin")
    reports.clear()
    check_in(config, http=_http(controller))
    assert reports == [False]


@pytest.mark.parametrize("body", [b"<html>captive portal</html>", b"[]", b"null", b'{"x": 1}'])
def test_an_unexpected_answer_is_a_controller_error(body):
    http = _http(lambda r: httpx.Response(200, content=body))
    with pytest.raises(ControllerError, match="unexpected answer"):
        check_in(CONFIG, http=http)


def test_an_error_answer_that_is_not_an_object_is_still_reported():
    http = _http(lambda r: httpx.Response(502, json=["bad gateway"]))
    with pytest.raises(ControllerError, match="502"):
        check_in(CONFIG, http=http)


def test_checkin_loop_keeps_going_after_a_network_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "collector.json"
    CONFIG.save(path)
    calls = []

    def flaky(session):
        calls.append(session)
        if len(calls) == 1:
            raise httpx.ConnectError("unreachable")
        raise KeyboardInterrupt  # stop the loop after the retry

    monkeypatch.setattr(cli.Session, "check_in", flaky)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["--config", str(path), "checkin", "--interval", "1"])
    assert len(calls) == 2
    assert "retrying" in capsys.readouterr().err


def test_checkin_once_still_fails_on_a_network_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "collector.json"
    CONFIG.save(path)

    def down(session):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(cli.Session, "check_in", down)
    assert cli.main(["--config", str(path), "checkin", "--once"]) == 1
    assert "unreachable" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_save_tightens_an_existing_config_file(tmp_path):
    path = tmp_path / "collector.json"
    path.write_text("{}")
    os.chmod(path, 0o644)
    CONFIG.save(path)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert CollectorConfig.load(path) == CONFIG


def test_an_identity_from_before_key_pairs_asks_to_enroll_again(tmp_path, capsys):
    path = tmp_path / "collector.json"
    old = {"controller_url": "https://c", "collector_id": "c1", "secret": "s", "customer": "A"}
    path.write_text(json.dumps(old))
    assert cli.main(["--config", str(path), "checkin", "--once"]) == 1
    assert "Enroll again" in capsys.readouterr().err


def test_a_relative_ca_file_is_saved_as_an_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ca.pem").write_text("")
    seen = {}

    def fake_enroll(url, token, *, proxy, ca_file):
        seen["ca_file"] = ca_file
        return CONFIG

    monkeypatch.setattr(cli, "enroll", fake_enroll)
    argv = ["--config", str(tmp_path / "c.json"), "enroll", "--controller", "https://c"]
    assert cli.main([*argv, "--token", "t", "--ca-file", "ca.pem"]) == 0
    assert seen["ca_file"] == str(tmp_path / "ca.pem")


def test_the_pin_is_taken_before_the_body_is_read(monkeypatch):
    # A connection the server closes loses its TLS details once the body is read.
    monkeypatch.setattr(client, "_peer_pin", lambda response: "seen-before-close")
    response = httpx.Response(200)
    client._remember_pin(response)
    monkeypatch.setattr(client, "_peer_pin", lambda response: None)
    assert client.server_pin(response) == "seen-before-close"
    assert client.new_http()._event_hooks["response"] == [client._remember_pin]

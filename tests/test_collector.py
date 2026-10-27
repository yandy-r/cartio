"""The collector on its own, against a stand-in controller."""

import os
import sys

import httpx
import pytest
from cartio_collector import cli
from cartio_collector.client import ControllerError, check_in, enroll
from cartio_collector.config import CollectorConfig

CONFIG = CollectorConfig(
    controller_url="https://controller.example",
    collector_id="00000000-0000-0000-0000-000000000001",
    secret="s3cret",
    customer="Acme",
)


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
    answer = {"collector_id": "c1", "secret": "s", "customer": "Acme"}
    config = enroll(url, "token", http=_http(lambda r: httpx.Response(201, json=answer)))
    assert config.collector_id == "c1"


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

    def flaky(config):
        calls.append(config)
        if len(calls) == 1:
            raise httpx.ConnectError("unreachable")
        raise KeyboardInterrupt  # stop the loop after the retry

    monkeypatch.setattr(cli, "check_in", flaky)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["--config", str(path), "checkin", "--interval", "1"])
    assert len(calls) == 2
    assert "retrying" in capsys.readouterr().err


def test_checkin_once_still_fails_on_a_network_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "collector.json"
    CONFIG.save(path)

    def down(config):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(cli, "check_in", down)
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

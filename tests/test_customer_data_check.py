"""The pre-commit check that keeps customer device data out of the repo.

Sample secrets are assembled at runtime so this file doesn't trip the check itself.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_customer_data.py"
_spec = importlib.util.spec_from_file_location("check_customer_data", _SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

SP = " "


@pytest.mark.parametrize(
    "line",
    [
        "username admin password" + SP + "7 0822455D0A16",
        "enable secret" + SP + "5 $1$mERr$hx5rVt7rPNoS4wqbXKX7m0",
        "username ops secret"
        + SP
        + "9 $9$nhEmQVczB7dqsO$X.HsgL6x1il0RxkOSSvyQYwucySCt7qFm4v7pqCxkKM",
        "snmp-server" + SP + "community Pub1ic RO",
        "    set password" + SP + "ENC SH2Bk7t5hZ3lPa9kE0Q==",
        "    set psksecret" + SP + "ENC 4mUcZzYp1qO1sQ==",
        "-----BEGIN RSA" + SP + "PRIVATE KEY-----",
    ],
)
def test_unmasked_device_secrets_are_caught(line):
    assert check.check_text(f"hostname r1\n{line}\n") != []


@pytest.mark.parametrize(
    "line",
    [
        "enable secret" + SP + "5 <masked:3fa1>",
        "snmp-server" + SP + "community **** RO",
        "    set password" + SP + "ENC MASKED",
        "secret = models.CharField(max_length=200)",
        "def check_secret(self, secret: str) -> bool:",
        "show running-config | include hostname",
    ],
)
def test_masked_values_and_ordinary_code_pass(line):
    assert check.check_text(line) == []


@pytest.mark.parametrize(
    "name",
    ["acme-core1-running-config.txt", "r1_startup-config", "show-run-r1.log", "SHOW_RUN.txt"],
)
def test_raw_device_files_are_blocked_by_name(name):
    assert check.check_name(f"samples/{name}") is not None


def test_sanitized_fixtures_are_allowed_by_name():
    assert check.check_name("tests/fixtures/sanitized/ios-running-config.txt") is None


def test_main_reports_and_fails(tmp_path, capsys):
    bad = tmp_path / "notes.txt"
    bad.write_text("snmp-server" + SP + "community Pub1ic RO\n")
    good = tmp_path / "ok.txt"
    good.write_text("nothing to see\n")
    assert check.main([str(good)]) == 0
    assert check.main([str(good), str(bad)]) == 1
    assert "notes.txt:1: unmasked SNMP community" in capsys.readouterr().err


def test_the_repo_itself_is_clean():
    assert check.main([]) == 0

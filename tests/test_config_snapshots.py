"""Configs are masked at the collector and scanned again on the controller (YAN-1714, ADR-0003)."""

import datetime
from types import SimpleNamespace

import pytest
from cartio_collector.ssh import CONFIG_COMMANDS, ScrapliDriver, capture_config
from cartio_controller.collectors.inventory import (
    KeyVersionError,
    UnmaskedConfigError,
    UnmaskedOutputError,
    store_config,
    store_outputs,
)
from cartio_controller.collectors.models import (
    AuditEvent,
    ConfigSnapshot,
    Customer,
    Device,
    Engagement,
    OnSiteDataError,
    Source,
)
from cartio_controller.engineers.compare import Comparison
from cartio_core.masking import MaskingKey
from django.db import models

from tests.test_parsers import outputs

pytestmark = pytest.mark.django_db

SP = " "
KEY = MaskingKey("k1", b"customer-a-masking-key-0123456789")
ROTATED = MaskingKey("k2", b"customer-a-rotated-key-0123456789")
# An IOS line with a secret in it, built here so the repo holds no literal one.
ENABLE = SP.join(["enable", "secret", "5", "$1$" + "a" * 26])


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _engagement(customer, name, day):
    return Engagement.objects.for_customer(customer).create(
        name=name, start_date=datetime.date(*day)
    )


def _device(engagement, name="rtr-1"):
    customer = engagement.customer
    source = Source.objects.for_customer(customer).create(
        engagement=engagement, kind="upload", name="configs"
    )
    device = Device.objects.for_customer(customer).create(
        engagement=engagement, name=name, platform="ios", source=source
    )
    return device, source


class FakeConnection:
    def __init__(self, config):
        self.config = config
        self.sent = []

    def send_command(self, command):
        self.sent.append(command)
        return SimpleNamespace(result=self.config, failed=False)

    def close(self):
        pass


def _captured(config, key=KEY, platform="ios"):
    """What the collector would upload for a device showing `config`."""
    connection = FakeConnection(config)
    masked = capture_config(ScrapliDriver(platform, connection), key)
    assert connection.sent == [CONFIG_COMMANDS[platform]]
    return masked


def test_a_captured_config_is_masked_before_it_leaves_the_collector():
    masked = _captured(f"hostname rtr-1\n{ENABLE}\n")
    assert "$1$" not in masked.text
    assert "<masked:k1:" in masked.text
    assert masked.key_version == "k1" and masked.secrets_masked == 1


def test_a_snapshot_keeps_the_masked_text_hash_platform_and_key_version(acme):
    device, source = _device(_engagement(acme, "Q2", (2026, 4, 1)))
    masked = _captured(f"hostname rtr-1\n{ENABLE}\n")
    snapshot = store_config(source, device, "ios", masked.text, key_version="k1", actor="dana")
    assert snapshot.content == masked.text
    assert snapshot.sha256 == masked.content_hash
    assert (snapshot.platform, snapshot.key_version, snapshot.key_rotated) == ("ios", "k1", False)
    event = AuditEvent.objects.for_customer(acme).get(action=AuditEvent.Action.CONFIG_STORED)
    assert (event.actor, event.object_name) == ("dana", "rtr-1")
    assert event.detail["sha256"] == masked.content_hash


def test_a_config_with_an_unmasked_secret_is_refused_and_logged(acme):
    device, source = _device(_engagement(acme, "Q2", (2026, 4, 1)))
    with pytest.raises(UnmaskedConfigError):
        store_config(source, device, "ios", f"hostname rtr-1\n{ENABLE}\n", key_version="k1")
    assert not ConfigSnapshot.objects.for_customer(acme).exists()
    event = AuditEvent.objects.for_customer(acme).get(action=AuditEvent.Action.CONFIG_REJECTED)
    assert event.object_name == "rtr-1"
    assert event.detail["lines"] == [2]
    assert event.detail["kinds"]
    # Line numbers and kinds only: the secret itself never reaches the log.
    assert "$1$" not in str(event.detail)


def test_outputs_with_an_unmasked_secret_are_logged_when_refused(acme):
    _, source = _device(_engagement(acme, "Q2", (2026, 4, 1)))
    lab = outputs("floor-sw1")
    lab["show version"] += ENABLE
    with pytest.raises(UnmaskedOutputError):
        store_outputs(source, "ios", lab)
    event = AuditEvent.objects.for_customer(acme).get(action=AuditEvent.Action.OUTPUTS_REJECTED)
    assert event.detail["command"] == "show version"
    assert "$1$" not in str(event.detail)


def test_placeholders_from_another_key_version_are_refused(acme):
    device, source = _device(_engagement(acme, "Q2", (2026, 4, 1)))
    masked = _captured(f"hostname rtr-1\n{ENABLE}\n", key=ROTATED)
    with pytest.raises(KeyVersionError):
        store_config(source, device, "ios", masked.text, key_version="k1")
    assert not ConfigSnapshot.objects.for_customer(acme).exists()


def test_the_first_snapshot_after_a_key_rotation_is_marked(acme):
    q2 = _engagement(acme, "Q2", (2026, 4, 1))
    q3 = _engagement(acme, "Q3", (2026, 7, 1))
    q4 = _engagement(acme, "Q4", (2026, 10, 1))
    config = f"hostname rtr-1\n{ENABLE}\n"
    stored = []
    for engagement, key in ((q2, KEY), (q3, ROTATED), (q4, ROTATED)):
        device, source = _device(engagement)
        masked = _captured(config, key=key)
        stored.append(store_config(source, device, "ios", masked.text, key_version=key.version))
    assert [s.key_rotated for s in stored] == [False, True, False]


def test_a_rotation_is_per_customer(acme):
    globex = Customer.objects.create(name="Globex")
    device, source = _device(_engagement(globex, "Q2", (2026, 4, 1)))
    store_config(source, device, "ios", _captured(ENABLE).text, key_version="k1")
    device, source = _device(_engagement(acme, "Q2", (2026, 4, 1)))
    snapshot = store_config(
        source, device, "ios", _captured(ENABLE, key=ROTATED).text, key_version="k2"
    )
    assert not snapshot.key_rotated


def _compare(customer, base_config, current_config, base_key, current_key):
    q2 = _engagement(customer, "Q2", (2026, 4, 1))
    q4 = _engagement(customer, "Q4", (2026, 10, 1))
    for engagement, config, key in ((q2, base_config, base_key), (q4, current_config, current_key)):
        device, source = _device(engagement)
        masked = _captured(config, key=key)
        store_config(source, device, "ios", masked.text, key_version=key.version)
    return Comparison(customer, q2, q4)


def test_a_changed_secret_shows_as_a_changed_placeholder(acme):
    changed = SP.join(["enable", "secret", "5", "$1$" + "b" * 26])
    found = _compare(acme, f"hostname rtr-1\n{ENABLE}\n", f"hostname rtr-1\n{changed}\n", KEY, KEY)
    [diff] = found.diffs
    assert [line.flag for line in diff.lines if line.sig == "+"] == ["secret"]
    assert all("$1$" not in line.text for line in diff.lines)


def test_a_key_rotation_alone_is_not_a_config_change(acme):
    config = f"hostname rtr-1\n{ENABLE}\n"
    assert _compare(acme, config, config, KEY, ROTATED).diffs == []


def test_on_site_customers_cant_store_configs(acme):
    device, source = _device(_engagement(acme, "Q2", (2026, 4, 1)))
    # Past the data mode action: these rows predate the switch, which would delete them.
    models.QuerySet.update(Customer.objects.filter(pk=acme.pk), data_mode="on_site")
    source = Source.objects.for_customer(acme).select_related("customer").get(pk=source.pk)
    with pytest.raises(OnSiteDataError):
        store_config(source, device, "ios", "hostname rtr-1\n", key_version="k1")

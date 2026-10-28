"""End to end: a real collector enrolls and checks in with a running controller."""

import pytest
from cartio_collector.cli import main
from cartio_collector.config import CollectorConfig
from cartio_controller.collectors.models import Collector, Customer

pytestmark = pytest.mark.django_db(transaction=True)


def test_collector_enrolls_and_checks_in(live_server, tmp_path, capsys):
    collector = Collector(customer=Customer.objects.create(name="Acme"), name="vm01")
    token = collector.issue_enrollment_token()
    collector.save()
    config_path = tmp_path / "collector.json"

    assert (
        main(
            [
                "--config",
                str(config_path),
                "enroll",
                "--controller",
                live_server.url,
                "--token",
                token,
            ]
        )
        == 0
    )
    saved = CollectorConfig.load(config_path)
    assert saved.collector_id == str(collector.id)
    assert saved.customer == "Acme"

    assert main(["--config", str(config_path), "checkin", "--once"]) == 0
    assert "Checked in: ok." in capsys.readouterr().out

    collector.refresh_from_db()
    assert collector.last_check_in is not None
    assert collector.check_ins.count() == 1


def test_collector_reports_a_refused_token(live_server, tmp_path, capsys):
    code = main(
        [
            "--config",
            str(tmp_path / "c.json"),
            "enroll",
            "--controller",
            live_server.url,
            "--token",
            "wrong",
        ]
    )
    assert code == 1
    assert "403" in capsys.readouterr().err

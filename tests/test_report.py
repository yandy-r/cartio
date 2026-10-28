"""The assessment report and its export (YAN-1666, DESIGN.md section 9)."""

import base64
import datetime
import io
import re
import zipfile
import zlib
from xml.etree import ElementTree

import pytest
from cartio_controller.collectors.models import (
    AuditEvent,
    Customer,
    Device,
    Engagement,
    Finding,
    Site,
)

from tests.test_engineer_ui import _engineer, _signed_in
from tests.test_findings_screen import _assessed

pytestmark = pytest.mark.django_db


@pytest.fixture
def engineer(client):
    user = _engineer(first_name="Dana", last_name="Reyes")
    _signed_in(client, user)
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _engagement(customer, name="Q4 review"):
    return Engagement.objects.for_customer(customer).create(
        name=name,
        status="active",
        start_date=datetime.date(2026, 10, 1),
        end_date=datetime.date(2026, 10, 28),
    )


def _url(engagement, path="", query=""):
    return f"/customers/{engagement.customer_id}/engagements/{engagement.pk}/report{path}{query}"


def _export(client, engagement, fmt, **extra):
    return client.post(_url(engagement, "/export"), {"format": fmt, **extra})


def _pdf_text(content: bytes) -> str:
    """The page streams of a PDF, inflated, for checking what it says."""
    text = []
    for stream in re.findall(rb"stream\r?\n(.*?)\s*endstream", content, re.S):
        data = stream.strip()
        if data.endswith(b"~>"):  # ReportLab writes page streams as ASCII85 over Flate.
            data = base64.a85decode(re.sub(rb"\s", b"", data[:-2]))
        try:
            text.append(zlib.decompress(data).decode("latin-1"))
        except zlib.error:
            text.append(data.decode("latin-1"))
    return "\n".join(text)


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        parts = [package.read(n).decode() for n in package.namelist() if n.endswith(".xml")]
    for part in parts:
        ElementTree.fromstring(part)  # noqa: S314 - our own output; checks it is well-formed.
    return "\n".join(parts)


@pytest.fixture
def assessed(acme):
    """Acme's engagement with two check findings, one edited, and a manual one."""
    engagement = _engagement(acme)
    site = Site.objects.for_customer(acme).create(name="HQ", location="Main street")
    devices = _assessed(engagement)
    for device in devices:
        device.site = site
        device.manufacturer = "Cisco"
        device.os_version = "17.9.4"
        device.serial = f"SER-{device.name}"
        device.save()
    Finding.objects.for_customer(acme).create(
        engagement=engagement,
        kind=Finding.Kind.CHECK,
        check_id="insecure-management",
        note="Telnet is still used by the backup job.",
    )
    manual = Finding.objects.for_customer(acme).create(
        engagement=engagement,
        kind=Finding.Kind.MANUAL,
        title="Spanning tree root on an access switch",
        severity="critical",
        recommendation="Set the root bridge priority on the core.",
        device_ids=[str(devices[2].pk)],
    )
    return engagement, devices, manual


def test_preview_shows_the_pages(client, engineer, assessed):
    engagement, devices, manual = assessed
    page = client.get(_url(engagement)).content.decode()
    assert "Network assessment" in page
    assert "Prepared by Dana Reyes · IT Cloud Network" in page
    assert "October 1, 2026 to October 28, 2026" in page
    # The edited set: the manual finding, the check findings and the engineer's note.
    assert "Spanning tree root on an access switch" in page
    assert "Insecure management protocols" in page
    assert "Telnet is still used by the backup job." in page
    assert 'report-h1">Device appendix' in page and "SER-core-sw-01" in page
    assert "3 findings included (with your edits, 1 added by an engineer)" in page
    assert 'aria-current="page"' in page and "/report" in page
    # Nothing internal: no device or finding ids.
    assert str(manual.pk) not in page
    assert all(str(d.pk) not in page for d in devices)


def test_left_out_dismissed_and_below_cut_findings_are_not_printed(client, engineer, assessed):
    engagement, _, manual = assessed
    acme = engagement.customer
    manual.include_in_report = False
    manual.save()
    Finding.objects.for_customer(acme).create(
        engagement=engagement,
        kind=Finding.Kind.CHECK,
        check_id="firmware-drift",
        removed_at=datetime.datetime(2026, 10, 20, tzinfo=datetime.UTC),
        removed_reason="Planned upgrade",
    )
    page = client.get(_url(engagement, query="?set=1&min=high")).content.decode()
    assert "Spanning tree root" not in page
    assert "Firmware" not in page
    assert "Insecure management protocols" in page
    # Unticked optional sections are left out.
    assert 'report-h1">Device appendix' not in page
    assert 'report-h1">Inventory summary' not in page
    assert 'report-h1">Findings' in page


def test_exports_are_audit_logged_and_carry_the_report(client, engineer, assessed):
    engagement, devices, manual = assessed
    acme = engagement.customer
    pdf = _export(client, engagement, "pdf", set="1", sections=["appendix"], min="low")
    assert pdf.status_code == 200
    assert pdf["Content-Type"] == "application/pdf"
    assert pdf["Content-Disposition"].startswith('attachment; filename="cartio-report-Acme-Q4')
    assert pdf.content.startswith(b"%PDF")
    text = _pdf_text(pdf.content)
    assert "Spanning tree root on an access switch" in text
    assert "SER-core-sw-01" in text

    docx = _export(client, engagement, "docx")
    assert docx.status_code == 200
    xml = _docx_text(docx.content)
    assert "Telnet is still used by the backup job." in xml
    assert 'w:styleId="Heading1"' in xml

    found_csv = _export(client, engagement, "csv").content.decode("utf-8-sig")
    assert found_csv.splitlines()[0].startswith("severity,title,check")
    assert "Critical,Spanning tree root on an access switch" in found_csv

    for content in (text, xml, found_csv):
        assert str(manual.pk) not in content
        assert all(str(d.pk) not in content for d in devices)

    events = AuditEvent.objects.for_customer(acme).filter(action="report.exported").order_by("at")
    assert [e.detail["format"] for e in events] == ["pdf", "docx", "csv"]
    assert events[0].detail["sections"] == ["appendix"]
    assert events[0].detail["findings"] == 3
    assert events[0].actor == engineer.username
    # The screen lists them as previous exports.
    page = client.get(_url(engagement)).content.decode()
    assert events[0].detail["file"] in page


def test_report_never_mixes_customers(client, engineer, assessed):
    engagement, _, _ = assessed
    other = Customer.objects.create(name="Globex")
    theirs = _engagement(other, "Globex review")
    Device.objects.for_customer(other).create(engagement=theirs, name="globex-core-01")
    Finding.objects.for_customer(other).create(
        engagement=theirs,
        kind=Finding.Kind.MANUAL,
        title="Globex only finding",
        severity="high",
        recommendation="Globex only.",
        device_ids=[],
    )
    page = client.get(_url(engagement)).content.decode()
    docx = _docx_text(_export(client, engagement, "docx").content)
    pdf = _pdf_text(_export(client, engagement, "pdf").content)
    for content in (page, docx, pdf):
        assert "globex-core-01" not in content and "Globex only" not in content
    # Another customer's engagement under this customer's URL doesn't resolve.
    crossed = f"/customers/{engagement.customer_id}/engagements/{theirs.pk}/report"
    assert client.get(crossed).status_code == 404
    assert client.post(crossed + "/export", {"format": "pdf"}).status_code == 404
    assert not AuditEvent.objects.for_customer(other).filter(action="report.exported").exists()


def test_bad_format_is_refused(client, engineer, assessed):
    engagement, _, _ = assessed
    assert _export(client, engagement, "exe").status_code == 400
    assert client.get(_url(engagement, "/export")).status_code == 405


def test_on_site_reports_are_not_built_on_the_controller(client, engineer):
    live = Customer.objects.create(
        name="Bluepeak", data_mode=Customer.DataMode.ON_SITE, live_view_allowed=True
    )
    engagement = _engagement(live)
    page = client.get(_url(engagement)).content.decode()
    assert "On site reports are built and saved on site" in page
    assert "Not saved yet" in page
    assert _export(client, engagement, "pdf").status_code == 404

    locked = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    engagement = _engagement(locked)
    page = client.get(_url(engagement)).content.decode()
    assert "Inside access only" in page or "inside access only" in page
    assert "Network assessment" not in page


def test_customer_without_engagement(client, engineer, acme):
    page = client.get(f"/customers/{acme.pk}/report").content.decode()
    assert "no engagement yet" in page


def test_report_needs_sign_in(client, assessed):
    engagement, _, _ = assessed
    assert client.get(_url(engagement)).status_code == 302
    assert _export(client, engagement, "pdf").status_code in (302, 403)


# The topology and changes pages (YAN-1745).


def test_topology_page_prints_each_sites_map(client, engineer, acme):
    from tests.test_sitemap import _lab

    engagement, hq, floor = _lab(acme)
    page = client.get(_url(engagement)).content.decode()
    assert 'report-h1">Topology' in page
    assert 'report-h2">HQ' in page and 'report-h2">Floor 2' in page
    assert "Figure 1. Layer 2 topology of Floor 2 from LLDP and CDP" in page
    assert "<svg" in page and "core-sw1" in page
    # The report's maps are drawings, not links to the Site map, and carry no ids.
    assert "sitemap-svg" not in page
    assert all(str(d.pk) not in page for d in Device.objects.for_customer(acme))

    pdf = _export(client, engagement, "pdf")
    text = _pdf_text(pdf.content)
    assert "Layer 2 topology of HQ" in text and "(core-sw1)" in text

    docx = _export(client, engagement, "docx").content
    xml = _docx_text(docx)
    assert "Layer 2 topology of HQ" in xml and "rIdFigure2" in xml
    with zipfile.ZipFile(io.BytesIO(docx)) as package:
        images = [n for n in package.namelist() if n.startswith("word/media/")]
        assert images == ["word/media/figure1.png", "word/media/figure2.png"]
        assert package.read(images[0]).startswith(b"\x89PNG")
        assert 'Extension="png"' in package.read("[Content_Types].xml").decode()
        rels = package.read("word/_rels/document.xml.rels").decode()
        assert 'Target="media/figure2.png"' in rels


def test_topology_is_left_out_without_devices_or_when_unticked(client, engineer, assessed):
    engagement, _, _ = assessed
    # The assessed devices are on HQ, with no links: the map still shows them.
    assert 'report-h1">Topology' in client.get(_url(engagement)).content.decode()
    page = client.get(_url(engagement, query="?set=1&sections=scope")).content.decode()
    assert 'report-h1">Topology' not in page and "report-map" not in page

    empty = _engagement(Customer.objects.create(name="Initech"))
    page = client.get(_url(empty)).content.decode()
    assert 'report-h1">Topology' not in page


def test_svg_figures_draw_the_icons_and_labels():
    from cartio_controller.engineers import figures
    from cartio_controller.engineers.sitemap import ROLES

    for _, icon, _ in ROLES.values():
        lines = figures.flatten(icon)
        assert lines and all(points for points, _ in lines)
    # Compact arc flags run into the next number.
    ((points, closed),) = figures.flatten("M4.5 10a11 11 0 0115 0")
    assert not closed and points[-1] == (19.5, 10)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect x="0" y="0" width="200" height="100" fill="#FAFBFC"/>'
        '<g transform="translate(10 20)" opacity="0.5"><line x1="0" y1="0" x2="50" y2="0" '
        'stroke="#E8590C" stroke-dasharray="7 5"/><text x="5" y="30" font-size="12">a &lt; b</text>'
        '<path d="M3 8h18v8H3z" fill="none" stroke="#0E1A24"/></g></svg>'
    )
    width, height, shapes = figures.parse(svg)
    assert (width, height) == (200, 100)
    line = next(s for s in shapes if s.kind == "line")
    assert line.at == (10, 20, 60, 20) and line.style.opacity == 0.5
    assert next(s for s in shapes if s.kind == "text").text == "a < b"
    # The engineer UI's Plex fonts load, so the DOCX map matches the screen.
    assert "Plex" in " ".join(figures._load_font("ibm-plex-mono-latin-500-normal", 12).getname())
    content, w, h = figures.png(svg, 400)
    assert content.startswith(b"\x89PNG") and (w, h) == (400, 200)
    drawing = figures.drawing(svg, 100, 100)
    assert (drawing.width, drawing.height) == (100, 50)


@pytest.fixture
def two_reviews(acme):
    from tests.test_compare import pair

    q2, q4 = pair.__wrapped__(acme)
    return q2, q4


def test_changes_page_compares_with_the_engagement_before(client, engineer, two_reviews):
    q2, q4 = two_reviews
    page = client.get(_url(q4)).content.decode()
    assert 'report-h1">Changes since the last assessment' in page
    assert "Compared with the previous assessment, Q2 review (April 14, 2026)." in page
    assert "Findings fixed" in page and "Insecure management protocols on core-sw-01" in page
    assert "core-sw-01 17.6.5 → 17.9.4a" in page
    assert "east-sw-02" in page and "legacy-sw" in page
    assert "Configurations changed" in page and "core-sw-01 (+" in page

    pdf = _pdf_text(_export(client, q4, "pdf").content)
    docx = _docx_text(_export(client, q4, "docx").content)
    for content in (pdf, docx):
        assert "Changes since the last assessment" in content
        assert "Configurations changed" in content
        # Configs are compared, but no config line or secret prints.
        assert "masked:" not in content and "hostname core-sw-01" not in content
    event = AuditEvent.objects.for_customer(q4.customer).filter(action="report.exported").first()
    assert event.detail["baseline"] == "Q2 review"


def test_changes_page_is_left_out_without_an_earlier_engagement(client, engineer, two_reviews):
    q2, q4 = two_reviews
    page = client.get(_url(q2)).content.decode()
    assert "Changes since the last assessment" in page  # the option, shown disabled
    assert "Needs an earlier engagement" in page
    assert 'report-h1">Changes' not in page
    page = client.get(_url(q4, query="?set=1&sections=scope")).content.decode()
    assert 'report-h1">Changes' not in page


def test_baseline_must_be_an_earlier_engagement_of_this_customer(client, engineer, two_reviews):
    q2, q4 = two_reviews
    later = _engagement(q4.customer, "Q1 2027 review")
    later.start_date, later.end_date = datetime.date(2027, 1, 10), datetime.date(2027, 1, 20)
    later.save()
    picked = client.get(_url(later, query=f"?baseline={q2.pk}")).content.decode()
    assert "Compared with an earlier assessment, Q2 review" in picked
    # A later engagement, itself, or another customer's isn't a baseline.
    other = _engagement(Customer.objects.create(name="Globex"), "Globex review")
    for baseline in (later, other):
        assert client.get(_url(q4, query=f"?baseline={baseline.pk}")).status_code == 404
    assert _export(client, q4, "pdf", baseline=str(other.pk)).status_code == 404
    assert client.get(_url(q4, query=f"?baseline={q4.pk}")).status_code == 404


def test_compare_adds_its_changes_to_the_report(client, engineer, two_reviews):
    q2, q4 = two_reviews
    compare = f"/customers/{q4.customer_id}/engagements/{q4.pk}/compare"
    page = client.get(compare).content.decode()
    link = f"/customers/{q4.customer_id}/engagements/{q4.pk}/report?baseline={q2.pk}"
    assert f'href="{link}"' in page and "Add to report" in page
    assert 'report-h1">Changes since the last assessment' in client.get(link).content.decode()
    # Not offered when the baseline is the later one.
    reversed_page = client.get(
        f"/customers/{q2.customer_id}/engagements/{q2.pk}/compare?baseline={q4.pk}"
    ).content.decode()
    assert "Add to report" not in reversed_page

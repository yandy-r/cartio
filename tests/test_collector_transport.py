"""The collector reaches a real controller over TLS: directly, through an explicit proxy, and
through a TLS-inspecting proxy (ADR-0009). The proxies are mitmproxy, which CI installs; set
CARTIO_REQUIRE_MITMPROXY=1 to fail instead of skip when it is missing.

The controller is the live test server behind a small TLS front with a test CA's certificate.
"""

import datetime
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest
from cartio_collector.cli import main
from cartio_collector.config import CollectorConfig
from cartio_controller.collectors.models import Collector, Customer
from cartio_core import signing
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = pytest.mark.django_db(transaction=True)

_PROXY_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def _name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _cert(subject, issuer, public_key, signer, *, ca, sans=()):
    # Python 3.13 verifies strictly (VERIFY_X509_STRICT), so give the extensions a real CA would.
    now = datetime.datetime.now(datetime.UTC)
    usage = dict.fromkeys(
        [
            "content_commitment",
            "key_encipherment",
            "data_encipherment",
            "key_agreement",
            "encipher_only",
            "decipher_only",
        ],
        False,
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=not ca, key_cert_sign=ca, crl_sign=ca, **usage),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()),
            critical=False,
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(list(sans)), critical=False)
    return builder.sign(signer, hashes.SHA256())


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


class TLSFront:
    """Terminates TLS with the test certificate and passes bytes on to the live server."""

    def __init__(self, backend_port, context):
        self.backend_port = backend_port
        self.context = context
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            front = self.context.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError):
            conn.close()
            return
        back = socket.create_connection(("localhost", self.backend_port))
        threading.Thread(target=self._pipe, args=(back, front), daemon=True).start()
        self._pipe(front, back)

    @staticmethod
    def _pipe(source, sink):
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, sink):
                try:
                    sock.close()
                except OSError:
                    pass

    def close(self):
        self.server.close()


@pytest.fixture
def controller(live_server, tmp_path):
    """An https controller URL, the test CA's file, and the controller's TLS key pin."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("Cartio test CA", "Cartio test CA", ca_key.public_key(), ca_key, ca=True)
    key = ec.generate_private_key(ec.SECP256R1())
    sans = [x509.DNSName("localhost")]
    server = _cert("localhost", "Cartio test CA", key.public_key(), ca_key, ca=False, sans=sans)
    (tmp_path / "server.pem").write_bytes(_pem(server))
    (tmp_path / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (tmp_path / "ca.pem").write_bytes(_pem(ca))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(tmp_path / "server.pem", tmp_path / "server.key")
    front = TLSFront(live_server.thread.port, context)
    pin = signing.spki_pin(server.public_bytes(serialization.Encoding.DER))
    yield f"https://localhost:{front.port}", str(tmp_path / "ca.pem"), pin
    front.close()


@pytest.fixture(autouse=True)
def no_ambient_proxy(monkeypatch):
    """The tests choose the proxy; one set on the machine running them mustn't interfere."""
    for name in (*_PROXY_ENV, "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mitmdump(tmp_path, controller):
    """Start mitmproxy as an explicit proxy with basic auth. Returns a function taking
    `inspect`: True re-signs TLS with mitmproxy's own CA, False passes TLS through untouched."""
    binary = shutil.which("mitmdump")
    if binary is None:
        if os.environ.get("CARTIO_REQUIRE_MITMPROXY") == "1":
            pytest.fail("mitmdump isn't installed, and CARTIO_REQUIRE_MITMPROXY=1")
        pytest.skip("mitmdump isn't installed")
    _, ca_file, _ = controller
    started = []

    def start(*, inspect):
        port = _free_port()
        confdir = tmp_path / f"mitm-{port}"
        args = [
            binary,
            "--quiet",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            str(port),
            "--proxyauth",
            "cartio:proxy-pass",
            "--set",
            f"confdir={confdir}",
            "--set",
            f"ssl_verify_upstream_trusted_ca={ca_file}",
        ]
        if not inspect:
            args += ["--ignore-hosts", ".*"]
        env = {k: v for k, v in os.environ.items() if k not in _PROXY_ENV}
        process = subprocess.Popen(args, env=env)  # noqa: S603 (a test's own proxy)
        started.append(process)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if process.poll() is not None:
                    pytest.fail("mitmdump exited")
                time.sleep(0.2)
        else:
            pytest.fail("mitmdump didn't start listening")
        return port, confdir / "mitmproxy-ca-cert.pem"

    yield start
    for process in started:
        process.terminate()
        process.wait(timeout=10)


def _new_collector():
    collector = Collector(customer=Customer.objects.create(name="Acme"), name="vm01")
    token = collector.issue_enrollment_token()
    collector.save()
    return collector, token


def _enroll(config_path, url, token, *extra):
    argv = ["--config", str(config_path), "enroll", "--controller", url, "--token", token]
    return main([*argv, *extra])


def _check_in(config_path):
    return main(["--config", str(config_path), "checkin", "--once"])


def test_direct_over_tls_pins_the_controllers_key(controller, tmp_path):
    url, ca_file, pin = controller
    collector, token = _new_collector()
    config_path = tmp_path / "collector.json"

    assert _enroll(config_path, url, token, "--ca-file", ca_file) == 0
    assert CollectorConfig.load(config_path).tls_pins == [pin]
    assert _check_in(config_path) == 0

    collector.refresh_from_db()
    assert collector.last_check_in is not None
    assert not collector.tls_inspected


def test_an_untrusted_controller_certificate_is_refused(controller, tmp_path, capsys):
    url, _, _ = controller
    _, token = _new_collector()
    # The test CA isn't in the OS store, so the collector refuses the connection.
    assert _enroll(tmp_path / "c.json", url, token) == 1
    assert "certificate" in capsys.readouterr().err.lower()


def test_through_an_explicit_proxy_with_basic_auth(controller, mitmdump, tmp_path, capsys):
    url, ca_file, pin = controller
    port, _ = mitmdump(inspect=False)
    collector, token = _new_collector()
    config_path = tmp_path / "collector.json"

    wrong = f"http://cartio:wrong@127.0.0.1:{port}"
    assert _enroll(config_path, url, token, "--ca-file", ca_file, "--proxy", wrong) == 1
    assert "407" in capsys.readouterr().err

    proxy = f"http://cartio:proxy-pass@127.0.0.1:{port}"
    assert _enroll(config_path, url, token, "--ca-file", ca_file, "--proxy", proxy) == 0
    # The proxy passed TLS through, so the collector saw the controller's own key.
    assert CollectorConfig.load(config_path).tls_pins == [pin]
    assert _check_in(config_path) == 0

    collector.refresh_from_db()
    assert collector.last_check_in is not None
    assert not collector.tls_inspected


def test_through_a_tls_inspecting_proxy_works_and_is_reported(
    controller, mitmdump, tmp_path, monkeypatch, settings, capsys
):
    url, ca_file, pin = controller
    port, mitm_ca = mitmdump(inspect=True)
    # The customer's inspection CA is trusted on the collector host, as with FortiGate.
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes((tmp_path / "ca.pem").read_bytes() + mitm_ca.read_bytes())
    # The controller publishes its pin, so inspection is seen even from the first request.
    settings.TLS_PINS = [pin]
    monkeypatch.setenv("HTTPS_PROXY", f"http://cartio:proxy-pass@127.0.0.1:{port}")
    collector, token = _new_collector()
    config_path = tmp_path / "collector.json"

    assert _enroll(config_path, url, token, "--ca-file", str(bundle)) == 0
    assert CollectorConfig.load(config_path).tls_pins == [pin]
    assert _check_in(config_path) == 0
    assert "inspects TLS" in capsys.readouterr().err

    # Signed requests survive inspection; the inspected path is reported, not blocked.
    collector.refresh_from_db()
    assert collector.last_check_in is not None
    assert collector.tls_inspected
    # The first report can't know yet (the key is seen in the answer), so it is corrected.
    assert [c.tls_inspected for c in collector.check_ins.order_by("at", "pk")] == [False, True]

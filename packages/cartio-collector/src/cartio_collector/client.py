"""Talks to the controller. The collector always connects out; it never listens.

Every request after enrollment is signed with the collector's private key (ADR-0009). TLS is
checked against the operating system's certificate store, so a customer's inspection CA that is
installed on the host works, and the controller's key is pinned: a different key on the path is
reported at check-in as TLS inspection, not blocked.
"""

from __future__ import annotations

import ipaddress
import platform
import socket
import ssl
from typing import Any
from urllib.parse import urlparse

import httpx
from cartio_core import sealing, signing
from cartio_core.protocol import (
    CHECKIN_PATH,
    ENROLL_PATH,
    JOBS_NEXT_PATH,
    MAX_WAIT,
    CheckIn,
    CheckInResponse,
    EnrollRequest,
    EnrollResponse,
    JobPoll,
    JobPollResponse,
    JobProgress,
    JobResult,
    JobStatus,
    job_path,
)

from . import __version__
from .config import CollectorConfig


class ControllerError(RuntimeError):
    """The controller refused a request or answered with something unexpected."""

    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body if isinstance(body, dict) else {}


class JobClosed(ControllerError):
    """The job already ended on the controller, most often because an engineer cancelled it."""


def _require_secure(url: str) -> None:
    """Signed requests and job data travel on every request, so plain http is only allowed to
    this machine."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _is_loopback(parsed.hostname):
        return
    raise ControllerError(
        f"controller URL must use https (http is allowed only for localhost): {url}"
    )


def _is_loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ssl_context(ca_file: str) -> ssl.SSLContext:
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def new_http(proxy: str = "", ca_file: str = "", auth: httpx.Auth | None = None) -> httpx.Client:
    """An HTTP client for the controller: OS trust store (or `ca_file`), and `proxy` if given,
    otherwise the HTTPS_PROXY and NO_PROXY environment variables."""
    return httpx.Client(
        timeout=30,
        verify=_ssl_context(ca_file),
        proxy=proxy or None,
        trust_env=not proxy,
        auth=auth,
        event_hooks={"response": [_remember_pin]},
    )


class Signer(httpx.Auth):
    """Signs each request with the collector's key: id, method, path, body, time and a nonce."""

    requires_request_body = True

    def __init__(self, collector_id: str, private_key: str) -> None:
        self._collector_id = collector_id
        self._private_key = private_key

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = signing.sign(
            self._private_key,
            self._collector_id,
            request.method,
            request.url.raw_path.decode("ascii"),
            request.content,
        )
        yield request


_PIN = "cartio_server_pin"


def _remember_pin(response: httpx.Response) -> None:
    # Runs before the body is read: once it is, a connection the server closes loses its TLS
    # details, and the pin with them.
    response.extensions[_PIN] = _peer_pin(response)


def server_pin(response: httpx.Response) -> str | None:
    """The pin of the TLS key that answered, or None over plain http."""
    if _PIN in response.extensions:
        return response.extensions[_PIN]
    return _peer_pin(response)


def _peer_pin(response: httpx.Response) -> str | None:
    stream = response.extensions.get("network_stream")
    ssl_object = stream.get_extra_info("ssl_object") if stream is not None else None
    if ssl_object is None:
        return None
    der = ssl_object.getpeercert(True)  # DER; the low-level object takes no keywords
    return signing.spki_pin(der) if der else None


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
        detail = body.get("error", response.text)
    except (ValueError, AttributeError):
        body, detail = None, response.text
    kind = ControllerError
    if response.status_code == 409 and isinstance(body, dict) and "status" in body:
        kind = JobClosed
    raise kind(f"controller answered {response.status_code}: {detail}", response.status_code, body)


def _parse(response: httpx.Response, kind: Any) -> Any:
    """Read a successful answer, which a proxy on a customer network may have replaced."""
    try:
        return kind.from_dict(response.json())
    except (ValueError, TypeError) as exc:
        raise ControllerError(f"controller sent an unexpected answer: {exc}") from exc


def enroll(
    controller_url: str,
    token: str,
    *,
    proxy: str = "",
    ca_file: str = "",
    http: httpx.Client | None = None,
) -> CollectorConfig:
    """Make this collector's key pair and exchange a one-time token and the public key for its
    identity. The private key stays in the returned config, on this host."""
    _require_secure(controller_url)
    controller_url = controller_url.rstrip("/")
    private_key = signing.new_private_key()
    seal_key = sealing.new_private_key()
    request = EnrollRequest(
        token=token,
        hostname=socket.gethostname(),
        public_key=signing.public_key_for(private_key),
        seal_key=sealing.public_key_for(seal_key),
    )
    client = http or new_http(proxy, ca_file)
    try:
        response = client.post(controller_url + ENROLL_PATH, json=request.to_dict())
        seen = server_pin(response)
    finally:
        if http is None:
            client.close()
    _raise_for_status(response)
    enrolled = _parse(response, EnrollResponse)
    # Pin what the controller publishes. If it publishes nothing, pin the key seen now.
    pins = enrolled.tls_pins or ([seen] if seen else [])
    return CollectorConfig(
        controller_url=controller_url,
        collector_id=enrolled.collector_id,
        customer=enrolled.customer,
        private_key=private_key,
        seal_key=seal_key,
        tls_pins=pins,
        proxy=proxy,
        ca_file=ca_file,
    )


class Session:
    """A collector's connection to the controller, kept open between check-ins."""

    def __init__(self, config: CollectorConfig, *, http: httpx.Client | None = None) -> None:
        _require_secure(config.controller_url)
        self.config = config
        self._signer = Signer(config.collector_id, config.private_key)
        self._own_http = http is None
        self._http = http or new_http(config.proxy, config.ca_file)
        # Whether the last answer came over a TLS key that isn't pinned.
        self.inspected = False

    def close(self) -> None:
        if self._own_http:
            self._http.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def post(
        self, path: str, body: dict[str, Any] | bytes, timeout: float | None = None
    ) -> httpx.Response:
        options: dict[str, Any] = {"auth": self._signer}
        if isinstance(body, bytes):
            options["content"] = body
            options["headers"] = {"Content-Type": "application/octet-stream"}
        else:
            options["json"] = body
        if timeout is not None:
            options["timeout"] = timeout
        response = self._http.post(self.config.controller_url + path, **options)
        seen = server_pin(response)
        if seen is not None and self.config.tls_pins:
            self.inspected = seen not in self.config.tls_pins
        _raise_for_status(response)
        return response

    def check_in(self) -> CheckInResponse:
        """Tell the controller this collector is alive, and receive any jobs."""
        # The report rides on the request, but the key is only seen in the answer. If the
        # answer shows the report was wrong (the first check-in, or the path changed), send
        # the corrected one at once.
        for _ in range(2):
            reported = self.inspected
            message = CheckIn(
                hostname=socket.gethostname(),
                version=__version__,
                platform=platform.platform(),
                tls_inspected=reported,
            )
            response = self.post(CHECKIN_PATH, message.to_dict())
            if self.inspected == reported:
                break
        return _parse(response, CheckInResponse)

    def poll(self, wait: int = MAX_WAIT) -> JobPollResponse:
        """Ask for the next job. The controller holds the request open up to `wait` seconds."""
        message = JobPoll(wait=wait, tls_inspected=self.inspected)
        # Leave the controller time to answer an empty poll before giving up on the connection.
        response = self.post(JOBS_NEXT_PATH, message.to_dict(), timeout=wait + 30)
        return _parse(response, JobPollResponse)

    def progress(self, job_id: str, message: str, percent: int | None = None) -> JobStatus:
        response = self.post(job_path(job_id, "progress"), JobProgress(message, percent).to_dict())
        return _parse(response, JobStatus)

    def upload_chunk(self, job_id: str, index: int, data: bytes) -> None:
        self.post(job_path(job_id, f"chunks/{index}"), data)

    def finish(self, job_id: str, result: JobResult) -> None:
        self.post(job_path(job_id, "result"), result.to_dict())


def check_in(config: CollectorConfig, *, http: httpx.Client | None = None) -> CheckInResponse:
    """Check in once."""
    with Session(config, http=http) as session:
        return session.check_in()

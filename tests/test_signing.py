"""The request signing scheme on its own (ADR-0009)."""

import pytest
from cartio_core import signing

BODY = b'{"hostname": "vm01"}'


@pytest.fixture
def key():
    return signing.new_private_key()


def _verify(key, header, method="POST", path="/api/v1/checkin", body=BODY, now=None):
    signing.verify(signing.public_key_for(key), signing.parse(header), method, path, body, now=now)


def test_a_signature_verifies(key):
    _verify(key, signing.sign(key, "c1", "POST", "/api/v1/checkin", BODY))


@pytest.mark.parametrize(
    "change",
    [
        {"method": "GET"},
        {"path": "/api/v1/enroll"},
        {"path": "/api/v1/checkin?x=1"},
        {"body": BODY + b" "},
    ],
)
def test_any_change_to_the_request_breaks_the_signature(key, change):
    header = signing.sign(key, "c1", "POST", "/api/v1/checkin", BODY)
    with pytest.raises(signing.SignatureError, match="doesn't match"):
        _verify(key, header, **change)


def test_another_id_or_key_breaks_the_signature(key):
    header = signing.sign(key, "c1", "POST", "/api/v1/checkin", BODY)
    with pytest.raises(signing.SignatureError):
        _verify(key, header.replace("id=c1", "id=c2"))
    with pytest.raises(signing.SignatureError):
        _verify(signing.new_private_key(), header)


def test_the_clock_window(key):
    header = signing.sign(key, "c1", "POST", "/api/v1/checkin", BODY, now=1_000_000)
    _verify(key, header, now=1_000_000 + signing.CLOCK_WINDOW)
    with pytest.raises(signing.SignatureError, match="clock window"):
        _verify(key, header, now=1_000_000 + signing.CLOCK_WINDOW + 1)
    with pytest.raises(signing.SignatureError, match="clock window"):
        _verify(key, header, now=1_000_000 - signing.CLOCK_WINDOW - 1)


def test_each_signature_has_a_fresh_nonce(key):
    nonces = {signing.parse(signing.sign(key, "c1", "POST", "/", b"")).nonce for _ in range(50)}
    assert len(nonces) == 50


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer abc.def",
        "Cartio-Ed25519 id=c1,ts=1,nonce=abcdefgh",
        "Cartio-Ed25519 id=c1,ts=soon,nonce=abcdefgh,sig=x",
        "Cartio-Ed25519 id=c1,ts=1,nonce=a-b,sig=x",
        "Cartio-Ed25519 id=,ts=1,nonce=abcdefgh,sig=x",
    ],
)
def test_malformed_headers_are_refused(header):
    with pytest.raises(signing.SignatureError):
        signing.parse(header)


def test_public_keys_are_checked(key):
    public = signing.public_key_for(key)
    assert signing.check_public_key(public) == public
    for bad in ["", "AAAA", "!!!", public + "AAAA"]:
        with pytest.raises(signing.SignatureError):
            signing.check_public_key(bad)

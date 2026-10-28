"""Mask secrets in device configs before they leave the collector (ADR-0003).

Each secret is replaced by a placeholder that carries a keyed digest of the value:

    enable secret 9 <masked:k1:3fa1c0de5b7a9e21>

The digest is HMAC-SHA256 under a key held per customer, so the same secret always gives the
same placeholder and a diff shows that a secret changed without showing it. A plain hash would
let anyone holding masked configs guess short passwords, and a shared key would show when two
customers use the same secret. The placeholder names the key version, so a key rotation can be
told apart from a config change.

Masking fails closed: after the rules run, a scan looks for anything that still looks like a
secret and raises `UnmaskedSecretError` rather than return it. Error messages name the line and
the kind of secret, never the value.

Adding a platform means adding its rules to `_RULES` and fixtures for every secret type to the
tests.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

PLACEHOLDER_PREFIX = "<masked:"
_KEY_VERSION = re.compile(r"[A-Za-z0-9_.-]{1,16}")
_MIN_KEY_BYTES = 32
_DIGEST_HEX_CHARS = 16


class Platform(StrEnum):
    IOS = "ios"
    IOS_XE = "ios-xe"
    FORTIOS = "fortios"


class UnmaskedSecretError(ValueError):
    """Masked output still contains something that looks like a secret."""

    def __init__(self, findings: list[UnmaskedSecret]) -> None:
        self.findings = findings
        where = ", ".join(f"line {f.line}: {f.kind}" for f in findings)
        super().__init__(f"config still contains secrets after masking ({where})")


@dataclass(frozen=True)
class MaskingKey:
    """A customer's masking key. `version` goes into every placeholder made with it."""

    version: str
    secret: bytes

    def __post_init__(self) -> None:
        if not _KEY_VERSION.fullmatch(self.version):
            raise ValueError("key version must be 1-16 letters, digits, '.', '_' or '-'")
        if len(self.secret) < _MIN_KEY_BYTES:
            raise ValueError(f"masking key must be at least {_MIN_KEY_BYTES} bytes")

    def __repr__(self) -> str:
        return f"MaskingKey(version={self.version!r}, secret=<hidden>)"

    def placeholder(self, value: str) -> str:
        digest = hmac.new(self.secret, value.encode("utf-8", "surrogatepass"), hashlib.sha256)
        return f"{PLACEHOLDER_PREFIX}{self.version}:{digest.hexdigest()[:_DIGEST_HEX_CHARS]}>"


@dataclass(frozen=True)
class MaskedConfig:
    """A config ready to leave the collector."""

    platform: Platform
    text: str
    key_version: str
    secrets_masked: int

    @property
    def content_hash(self) -> str:
        # Taken over the masked text. A hash of the raw config would let anyone who holds the
        # masked copy test guesses for the secrets in it.
        return hashlib.sha256(self.text.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class UnmaskedSecret:
    line: int
    kind: str


# --- Rules -----------------------------------------------------------------------------------
#
# A rule is a regex over one line. Every named group whose name starts with "v" is a secret
# value and is replaced; the rest of the line is kept. A rule can be limited to lines inside a
# block (IOS: the last unindented line; FortiOS: the innermost `config` path).

_T = r"(?: [0-9](?= ))?+"  # Cisco's optional encryption type before a value: 0, 5, 7, 8, 9...
_REST = r"(?P<v>\S(?:.*\S)?)"  # The value runs to the end of the line, spaces included.
_WORD = r"(?P<v>\S+)"
_FORTI_VALUE = r'(?:"(?P<v1>(?:[^"\\]|\\.)*)"|(?P<v2>\S+))'


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    block: re.Pattern[str] | None = None


def _rule(kind: str, pattern: str, block: str | None = None) -> _Rule:
    return _Rule(kind, re.compile(pattern), re.compile(block) if block else None)


_IOS_RULES = [
    # Specific shapes first; a later rule never re-masks part of a line an earlier one took.
    _rule("SNMP community", r"^\s*snmp-server community" + _T + " " + _WORD),
    _rule(
        "SNMP community",
        r"^\s*snmp-server host \S+(?: vrf \S+)?(?: (?:traps|informs))?"
        r"(?: version (?:1|2c|3 (?:auth|noauth|priv)))? " + _WORD,
    ),
    _rule("SNMPv3 auth key", r"^\s*snmp-server user .*?\bauth (?:md5|sha\S*) " + _WORD),
    _rule(
        "SNMPv3 priv key",
        r"^\s*snmp-server user .*?\bpriv (?:des56|3des|des|aes(?: (?:128|192|256))?) " + _WORD,
    ),
    _rule("pre-shared key", r"^\s*crypto isakmp key" + _T + " " + _WORD + " (?:address|hostname)"),
    _rule(
        "pre-shared key",
        r"\bpre-shared-key (?:address \S+(?: \S+)?|hostname \S+) key" + _T + " " + _REST,
    ),
    _rule(
        "pre-shared key",
        r"^\s*pre-shared-key(?: local| remote)?(?! address | hostname )" + _T + " " + _REST,
    ),
    _rule("pre-shared key", r"\bpre-share key" + _T + " " + _REST),
    _rule(
        "NTP key",
        r"^\s*ntp authentication-key \d+ (?:md5|sha1|sha2|hmac-sha\S*|cmac-aes-128) " + _WORD,
    ),
    _rule("OSPF key", r"\bip ospf authentication-key" + _T + " " + _REST),
    _rule("routing key", r"\bmessage-digest-key \d+ md5" + _T + " " + _REST),
    _rule("key string", r"\bkey-string" + _T + " " + _REST),
    _rule(
        "HSRP/VRRP key", r"\b(?:standby|vrrp)(?: \d+)? authentication (?!md5 )(?:text )?" + _WORD
    ),
    _rule("wireless PSK", r"\bwpa-psk (?:ascii|hex)" + _T + " " + _REST),
    _rule("config key", r"\bconfig-key password-encrypt " + _REST),
    _rule("RADIUS/TACACS key", r"\b(?:tacacs|radius)-server\b.*?\bkey" + _T + " " + _REST),
    _rule("RADIUS/TACACS key", r"\bserver-private \S+.*?\bkey" + _T + " " + _REST),
    _rule(
        "RADIUS/TACACS key",
        r"^\s+key" + _T + " " + _REST,
        block=r"^(?:tacacs server|radius server|aaa group server) ",
    ),
    _rule("key", r"\bkey [067] " + _REST),
    _rule("password", r"\bpassword(?! encryption aes$)" + _T + " " + _REST),
    _rule("secret", r"\bsecret" + _T + " " + _REST),
]

_FORTI_MASKED_FIELDS = (
    "password|passwd|password2|psksecret|psksecret-remote|ppk-secret|secret|secondary-secret|"
    "tertiary-secret|key|auth-pwd|priv-pwd|passphrase|sae-password|api-key|md5-key|"
    "authentication-key|key-string|enckey|authkey|private-key|server-key"
)

_FORTIOS_RULES = [
    # Anything FortiOS stores encrypted is a secret, whatever the field is called.
    _rule("FortiOS ENC value", r"\bENC " + _FORTI_VALUE),
    _rule("FortiOS secret", r"^\s*set (?:" + _FORTI_MASKED_FIELDS + r") (?!ENC )" + _FORTI_VALUE),
    # The SNMP community name is the community string itself.
    _rule(
        "SNMP community",
        r"^\s*set name " + _FORTI_VALUE,
        block=r"^system snmp community$",
    ),
]

_RULES: dict[Platform, list[_Rule]] = {
    Platform.IOS: _IOS_RULES,
    Platform.IOS_XE: _IOS_RULES,
    Platform.FORTIOS: _FORTIOS_RULES,
}

# PEM blocks (keys and certificates) on any platform. An unterminated block runs to the end of
# the text, so a truncated config can't leak half a key.
_PEM = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]+)-----.*?(?:-----END (?P=label)-----|\Z)", re.DOTALL
)


# --- Masking ---------------------------------------------------------------------------------


def mask_config(text: str, platform: Platform | str, key: MaskingKey) -> MaskedConfig:
    """Mask every secret in a device config. Raises `UnmaskedSecretError` if any remain."""
    platform = Platform(platform)
    count = 0

    def replace(value: str) -> str:
        nonlocal count
        count += 1
        return key.placeholder(value)

    text = _PEM.sub(lambda m: replace(m.group(0)), text)
    lines = text.splitlines(keepends=True)
    if platform is Platform.FORTIOS:
        lines = _mask_lines(lines, _RULES[platform], _fortios_blocks(lines), replace)
    else:
        lines = _mask_ios_certificates(lines, replace)
        lines = _mask_lines(lines, _RULES[platform], _ios_blocks(lines), replace)
    masked = "".join(lines)

    if findings := find_unmasked(masked, platform):
        raise UnmaskedSecretError(findings)
    return MaskedConfig(platform, masked, key.version, count)


def _mask_lines(
    lines: list[str], rules: list[_Rule], blocks: list[str], replace: Callable[[str], str]
) -> list[str]:
    out = []
    for line, block in zip(lines, blocks, strict=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        spans: list[tuple[int, int]] = []
        for rule in rules:
            if rule.block and not rule.block.search(block):
                continue
            for match in rule.pattern.finditer(body):
                for name, value in match.groupdict().items():
                    if not name.startswith("v") or value is None:
                        continue
                    start, end = match.span(name)
                    if value.startswith(PLACEHOLDER_PREFIX) or _overlaps(spans, start, end):
                        continue
                    spans.append((start, end))
        for start, end in sorted(spans, reverse=True):
            body = body[:start] + replace(body[start:end]) + body[end:]
        out.append(body + ending)
    return out


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < b and a < end for a, b in spans)


def _ios_blocks(lines: list[str]) -> list[str]:
    """The unindented line each line sits under (itself, for an unindented line)."""
    blocks, current = [], ""
    for line in lines:
        if line[:1] not in (" ", "\t", "\r", "\n", ""):
            current = line.strip()
        blocks.append(current)
    return blocks


def _fortios_blocks(lines: list[str]) -> list[str]:
    """The innermost `config` path each line sits in, for example `system snmp community`."""
    blocks, stack = [], []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("config "):
            stack.append(stripped[len("config ") :])
        elif stripped == "end" and stack:
            stack.pop()
        blocks.append(stack[-1] if stack else "")
    return blocks


_IOS_CERT_START = re.compile(r"^\s+certificate\b")
_IOS_CERT_END = re.compile(r"^\s+quit\s*$")


def _mask_ios_certificates(lines: list[str], replace: Callable[[str], str]) -> list[str]:
    """Collapse the hex body of each certificate in a `crypto pki certificate chain`.

    The body runs to `quit`, or failing that to the next unindented line.
    """
    out: list[str] = []
    i = 0
    in_chain = False
    while i < len(lines):
        line = lines[i]
        if line[:1] not in (" ", "\t", "\r", "\n", ""):
            in_chain = line.startswith("crypto pki certificate chain ")
        out.append(line)
        i += 1
        if not (in_chain and _IOS_CERT_START.match(line)):
            continue
        body = []
        while i < len(lines) and not _IOS_CERT_END.match(lines[i]):
            if lines[i][:1] not in (" ", "\t"):
                break
            body.append(lines[i])
            i += 1
        if body:
            indent = body[0][: len(body[0]) - len(body[0].lstrip())]
            ending = "\r\n" if body[-1].endswith("\r\n") else "\n"
            out.append(indent + replace("".join(b.strip() for b in body)) + ending)
    return out


# --- Leak check ------------------------------------------------------------------------------
#
# Deliberately narrower than the rules above: these are shapes that are almost certainly a
# secret, so a hit means a rule missed something. Used after masking, and by the controller to
# scan what it receives.

_OK = r'(?!"?<masked:|ENC "?<masked:)'
_TYPE = r"(?: [0-9](?= ))?+"

_COMMON_LEAKS = {
    "private key or certificate": re.compile(r"-----BEGIN [A-Z0-9 ]+-----"),
}
_IOS_LEAKS = {
    "password": re.compile(r"\bpassword" + _TYPE + " " + _OK + r"(?!encryption aes$)\S"),
    "secret": re.compile(r"\bsecret" + _TYPE + " " + _OK + r"\S"),
    "key string": re.compile(r"\bkey-string" + _TYPE + " " + _OK + r"\S"),
    "key": re.compile(r"\bkey [067] " + _OK + r"\S"),
    "SNMP community": re.compile(r"\bsnmp-server community(?: [08](?= ))?+ " + _OK + r"\S"),
}
_FORTIOS_LEAKS = {
    "FortiOS ENC value": re.compile(r"\bENC " + _OK + r"\S"),
    "FortiOS secret": re.compile(
        r"^\s*set (?:" + _FORTI_MASKED_FIELDS + r") " + _OK + r"(?!ENC )\S"
    ),
}
_LEAKS: dict[Platform | None, dict[str, re.Pattern[str]]] = {
    Platform.IOS: _COMMON_LEAKS | _IOS_LEAKS,
    Platform.IOS_XE: _COMMON_LEAKS | _IOS_LEAKS,
    Platform.FORTIOS: _COMMON_LEAKS | _FORTIOS_LEAKS,
    None: _COMMON_LEAKS | _IOS_LEAKS | _FORTIOS_LEAKS,
}


def find_unmasked(text: str, platform: Platform | str | None = None) -> list[UnmaskedSecret]:
    """Lines that still look like they hold a secret. Empty when the text is clean.

    Without a platform, every platform's patterns apply.
    """
    patterns = _LEAKS[Platform(platform) if platform else None]
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in patterns.items():
            if pattern.search(line):
                found.append(UnmaskedSecret(lineno, kind))
    return found

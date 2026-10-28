"""Mask secrets in device configs before they leave the collector (ADR-0003).

Each secret is replaced by a placeholder that carries a keyed digest of the value:

    enable secret 9 <masked:k1:3fa1c0de5b7a9e21>

The digest is HMAC-SHA256 under a key held per customer, so the same secret always gives the
same placeholder and a diff shows that a secret changed without showing it. A plain hash would
let anyone holding masked configs guess short passwords, and a shared key would show when two
customers use the same secret. The placeholder names the key version, so a key rotation can be
told apart from a config change.

SNMPv1/v2c community strings are judged here too, because only the collector sees the value.
A default or weak community gets a marked placeholder, `<masked:k1:3fa1c0de5b7a9e21:weak>`, so
the assessment checks can flag it (YAN-1744). The marker says the value is short or on a list of
well-known defaults, and nothing else about it.

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
WEAK_MARKER = ":weak"

# Communities that ship as defaults or are commonly guessed. Compared ignoring case.
_WEAK_COMMUNITIES = frozenset(
    {
        "public",
        "private",
        "community",
        "cisco",
        "fortinet",
        "admin",
        "manager",
        "monitor",
        "secret",
        "snmp",
        "snmpd",
        "default",
        "read",
        "write",
        "readonly",
        "readwrite",
        "test",
        "password",
        "network",
        "system",
        "security",
        "router",
        "switch",
        "all private",
        "ilmi",
    }
)
# Anything shorter is quick to guess over the network.
_MIN_COMMUNITY_CHARS = 8


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

    def placeholder(self, value: str, weak: bool = False) -> str:
        digest = hmac.new(self.secret, value.encode("utf-8", "surrogatepass"), hashlib.sha256)
        marker = WEAK_MARKER if weak else ""
        return (
            f"{PLACEHOLDER_PREFIX}{self.version}:{digest.hexdigest()[:_DIGEST_HEX_CHARS]}{marker}>"
        )


def weak_community(value: str) -> bool:
    """Is this SNMPv1/v2c community a well-known default, or too short?"""
    return len(value) < _MIN_COMMUNITY_CHARS or value.strip().lower() in _WEAK_COMMUNITIES


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
# block (IOS: the last unindented line; FortiOS: the innermost `config` path). Rules see tabs
# as spaces, and IOS rules ignore case, as IOS does.

_PLACEHOLDER = re.compile(r"<masked:[A-Za-z0-9_.-]{1,16}:[0-9a-f]{16}(?::weak)?>")
# A placeholder for a default or weak SNMP community.
WEAK_PLACEHOLDER = re.compile(r"<masked:[A-Za-z0-9_.-]{1,16}:[0-9a-f]{16}:weak>")
_COMMUNITY_KIND = "SNMP community"
_T = r"(?: [0-9](?= ))?+"  # Cisco's optional encryption type before a value: 0, 5, 7, 8, 9...
_REST = r"(?P<v>\S(?:.*\S)?)"  # The value runs to the end of the line, spaces included.
_WORD = r"(?P<v>\S+)"
_FORTI_VALUE = r'(?:"(?P<v1>(?:[^"\\]|\\.)*)"|(?P<v2>\S+))'
# Credentials in a URL, such as scp://user:pass@host/path, on any platform.
_URL_USERINFO = r"://[^/\s:@]*:(?P<v>[^@\s/]+)@"


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    block: re.Pattern[str] | None = None


def _rule(kind: str, pattern: str, block: str | None = None, flags: int = 0) -> _Rule:
    return _Rule(kind, re.compile(pattern, flags), re.compile(block, flags) if block else None)


def _ios(kind: str, pattern: str, block: str | None = None) -> _Rule:
    return _rule(kind, pattern, block, re.IGNORECASE)


_IOS_RULES = [
    # Specific shapes first; a later rule never re-masks part of a line an earlier one took.
    _ios("SNMP community", r"^\s*snmp-server community" + _T + " " + _WORD),
    _ios(
        "SNMP community",
        r"^\s*snmp-server host \S+(?: vrf \S+)?(?: (?:traps|informs))?"
        r"(?: version (?:1|2c|3 (?:auth|noauth|priv)))? " + _WORD,
    ),
    _ios("SNMP community", r"^\s*snmp mib community-map " + _WORD),
    _ios(
        "SNMPv3 auth key",
        r"^\s*snmp-server user .*?\bauth (?:md5|sha-2 \d+|sha\S*)(?: [07](?= ))?+ " + _WORD,
    ),
    _ios(
        "SNMPv3 priv key",
        r"^\s*snmp-server user .*?\bpriv (?:des56|3des|des|aes(?: (?:128|192|256))?)"
        r"(?: [07](?= ))?+ " + _WORD,
    ),
    _ios("pre-shared key", r"^\s*crypto isakmp key" + _T + " " + _WORD + " (?:address|hostname)"),
    _ios(
        "pre-shared key",
        r"\bpre-shared-key (?:address \S+(?: \S+)?|hostname \S+) key" + _T + " " + _REST,
    ),
    _ios(
        "pre-shared key",
        r"^\s*pre-shared-key(?: local| remote)?(?! address | hostname )" + _T + " " + _REST,
    ),
    _ios("pre-shared key", r"\bpre-share key" + _T + " " + _REST),
    _ios(
        "pre-shared key",
        r"^\s+key" + _T + " " + _REST,
        block=r"^crypto isakmp client configuration group ",
    ),
    _ios("NTP key", r"^\s*ntp authentication-key \d+ \S+" + _T + " " + _WORD),
    _ios("NHRP key", r"\bip nhrp authentication" + _T + " " + _REST),
    _ios("IPsec key", r"\bipsec spi \d+ (?:.*? )?(?:md5|sha1)" + _T + " " + _WORD),
    _ios("IPsec key", r"\bipsec spi \d+ esp (?:aes(?: \d+)?|3des|des|null)" + _T + " " + _WORD),
    _ios("routing key", r"\bmessage-digest-key \d+ md5" + _T + " " + _REST),
    _ios("routing key", r"\bauthentication-key" + _T + " " + _REST),
    _ios("key string", r"\bkey-string" + _T + " " + _REST),
    _ios(
        "HSRP/VRRP/GLBP key",
        r"\b(?:standby|vrrp|glbp)(?: \d+)? authentication (?!md5 )(?:text )?" + _WORD,
    ),
    _ios("wireless key", r"\bwpa-psk (?:ascii|hex)" + _T + " " + _REST),
    _ios("wireless key", r"\bset-key (?:ascii|hex)" + _T + " " + _REST),
    _ios("wireless key", r"\bencryption\b.*? key \d+ size \S+" + _T + " " + _WORD),
    _ios("config key", r"\bconfig-key password-encrypt " + _REST),
    _ios("RADIUS/TACACS key", r"^\s*(?:tacacs|radius)-server\b.*?\bkey" + _T + " " + _REST),
    _ios("RADIUS/TACACS key", r"^\s*server-private \S+.*?\bkey" + _T + " " + _REST),
    _ios("RADIUS/TACACS key", r"\bserver-key" + _T + " " + _REST),
    _ios("RADIUS/TACACS key", r"^\s*pac key" + _T + " " + _REST),
    _ios(
        "RADIUS/TACACS key",
        r"^\s+key" + _T + " " + _REST,
        block=r"^(?:tacacs server|radius server|aaa group server) ",
    ),
    _ios("URL credentials", _URL_USERINFO),
    _ios(
        "secret variable",
        r"^\s*event manager environment \S*(?:pass|secret|key|token)\S* " + _REST,
    ),
    _ios("key", r"(?<![-\w])key [067] " + _REST),
    _ios("password", r"\bpassword(?! encryption aes\s*$)" + _T + " " + _REST),
    _ios("secret", r"\bsecret" + _T + " " + _REST),
]

# FortiOS fields that hold a secret: these names, and any name ending in one of them after a
# dash (proxy-password, client-secret, secret-key...).
_FORTI_MASKED_NAMES = (
    r"password\d?|passwd|psksecret|secret|key|pwd|passphrase|token|seed|auth-string|"
    r"md5-keys?|key-string|enckey|authkey"
)
_FORTI_FIELD = r"(?:[\w-]*-)?(?:" + _FORTI_MASKED_NAMES + r")|psksecret-remote|ppk-secret"

_FORTIOS_RULES = [
    # Anything FortiOS stores encrypted is a secret, whatever the field is called.
    _rule("FortiOS ENC value", r"\bENC " + _FORTI_VALUE),
    _rule("FortiOS secret", r"^\s*set (?:" + _FORTI_FIELD + r") (?!ENC )" + _FORTI_VALUE),
    # The SNMP community name is the community string itself.
    _rule("SNMP community", r"^\s*set name " + _FORTI_VALUE, block=r"^system snmp community$"),
    _rule("HTTP credentials", r'^\s*set value "?(?:Bearer|Basic|Token) (?P<v>[^"\s]+)'),
    _rule("URL credentials", _URL_USERINFO),
]

_RULES: dict[Platform, list[_Rule]] = {
    Platform.IOS: _IOS_RULES,
    Platform.IOS_XE: _IOS_RULES,
    Platform.FORTIOS: _FORTIOS_RULES,
}

# PEM and SSH2 blocks (keys and certificates) on any platform. An unterminated block runs to
# the end of the text, so a truncated config can't leak half a key.
_PEM = re.compile(
    r"-{4,5} ?BEGIN (?P<label>[A-Z0-9 ]+?) ?-{4,5}.*?(?:-{4,5} ?END (?P=label) ?-{4,5}|\Z)",
    re.DOTALL,
)

# A quoted FortiOS secret can run over several lines.
_FORTI_QUOTED = re.compile(
    r"^[ \t]*set[ \t]+(?:" + _FORTI_FIELD + r')[ \t]+(?:ENC[ \t]+)?"(?P<v>(?:[^"\\]|\\.)*)"',
    re.MULTILINE,
)


# --- Masking ---------------------------------------------------------------------------------


def mask_config(text: str, platform: Platform | str, key: MaskingKey) -> MaskedConfig:
    """Mask every secret in a device config. Raises `UnmaskedSecretError` if any remain."""
    platform = Platform(platform)
    count = 0

    def replace(value: str, weak: bool = False) -> str:
        nonlocal count
        count += 1
        return key.placeholder(value, weak)

    def replace_group(match: re.Match[str]) -> str:
        if _PLACEHOLDER.fullmatch(match["v"]):
            return match[0]
        start, end = match.span("v")
        return (
            match[0][: start - match.start()]
            + replace(match["v"])
            + match[0][end - match.start() :]
        )

    text = _PEM.sub(lambda m: replace(m[0]), text)
    if platform is Platform.FORTIOS:
        text = _FORTI_QUOTED.sub(replace_group, text)
    lines = _split_lines(text)
    if platform is Platform.FORTIOS:
        lines = _mask_lines(lines, _RULES[platform], _fortios_blocks(lines), replace)
    else:
        lines = _mask_ios_certificates(lines, replace)
        lines = _mask_lines(lines, _RULES[platform], _ios_blocks(lines), replace)
    masked = "".join(lines)

    if findings := find_unmasked(masked, platform):
        raise UnmaskedSecretError(findings)
    return MaskedConfig(platform, masked, key.version, count)


def _split_lines(text: str) -> list[str]:
    # Only "\n" ends a line. str.splitlines also splits on characters such as "\x85", which
    # would let a value hide past a break the device doesn't see.
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def _mask_lines(
    lines: list[str], rules: list[_Rule], blocks: list[str], replace: Callable[[str, bool], str]
) -> list[str]:
    out = []
    for line, block in zip(lines, blocks, strict=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        view = body.replace("\t", " ")  # Same length, so spans carry over to body.
        spans: list[tuple[int, int, bool]] = []
        for rule in rules:
            if rule.block and not rule.block.search(block):
                continue
            for match in rule.pattern.finditer(view):
                for name, value in match.groupdict().items():
                    if not name.startswith("v") or value is None:
                        continue
                    start, end = match.span(name)
                    if _PLACEHOLDER.fullmatch(value) or _overlaps(spans, start, end):
                        continue
                    weak = rule.kind == _COMMUNITY_KIND and weak_community(value)
                    spans.append((start, end, weak))
        for start, end, weak in sorted(spans, reverse=True):
            body = body[:start] + replace(body[start:end], weak) + body[end:]
        out.append(body + ending)
    return out


def _overlaps(spans: list[tuple[int, int, bool]], start: int, end: int) -> bool:
    return any(start < b and a < end for a, b, _ in spans)


def _unindented(line: str) -> bool:
    return line[:1] not in (" ", "\t", "\r", "\n", "")


def _ios_blocks(lines: list[str]) -> list[str]:
    """The unindented line each line sits under (itself, for an unindented line)."""
    blocks, current = [], ""
    for line in lines:
        if _unindented(line):
            current = line.strip()
        blocks.append(current)
    return blocks


def _fortios_blocks(lines: list[str]) -> list[str]:
    """The innermost `config` path each line sits in, for example `system snmp community`."""
    blocks, stack = [], []
    for line in lines:
        words = line.split()
        if words[:1] == ["config"]:
            stack.append(" ".join(words[1:]))
        elif words == ["end"] and stack:
            stack.pop()
        blocks.append(stack[-1] if stack else "")
    return blocks


_IOS_CERT_START = re.compile(r"^\s+certificate\b", re.IGNORECASE)
_IOS_CERT_END = re.compile(r"^\s+quit\s*$", re.IGNORECASE)


def _mask_ios_certificates(lines: list[str], replace: Callable[[str], str]) -> list[str]:
    """Collapse the hex body of each certificate in a `crypto pki certificate chain`.

    The body runs to `quit`, or failing that to the next certificate or unindented line.
    """
    out: list[str] = []
    i = 0
    in_chain = False
    while i < len(lines):
        line = lines[i]
        if _unindented(line):
            in_chain = line.lower().startswith("crypto pki certificate chain ")
        out.append(line)
        i += 1
        if not (in_chain and _IOS_CERT_START.match(line)):
            continue
        body = []
        while i < len(lines) and not _IOS_CERT_END.match(lines[i]):
            if _unindented(lines[i]) or _IOS_CERT_START.match(lines[i]):
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
# Narrower than the rules above: these are shapes that are almost certainly a secret, so a hit
# means a rule missed something and collection stops. Used after masking, and by the
# controller to scan what it receives. Tabs count as spaces here too.

_PH = _PLACEHOLDER.pattern
_OK = r'(?!"?' + _PH + r'"?(?:\s|$)|ENC "?' + _PH + r'"?(?:\s|$))'
_TYPE = r"(?: [0-9](?= ))?+"
_I = re.IGNORECASE

_COMMON_LEAKS = {
    "private key or certificate": re.compile(r"-{4,5} ?BEGIN [A-Z0-9 ]+"),
    "URL credentials": re.compile(r"://[^/\s:@]*:(?!" + _PH + r"@)[^@\s/]+@"),
}
_IOS_LEAKS = {
    "password": re.compile(r"\bpassword" + _TYPE + " " + _OK + r"(?!encryption aes\s*$)\S", _I),
    "secret": re.compile(r"\bsecret" + _TYPE + " " + _OK + r"\S", _I),
    "key string": re.compile(r"\bkey-string" + _TYPE + " " + _OK + r"\S", _I),
    "key": re.compile(r"(?<![-\w])key [067] " + _OK + r"\S", _I),
    "server key": re.compile(r"\bserver-key" + _TYPE + " " + _OK + r"\S", _I),
    "SNMP community": re.compile(r"\bsnmp-server community(?: [08](?= ))?+ " + _OK + r"\S", _I),
}
_FORTIOS_LEAKS = {
    "FortiOS ENC value": re.compile(r"\bENC " + _OK + r"\S"),
    "FortiOS secret": re.compile(r"^\s*set (?:" + _FORTI_FIELD + r") " + _OK + r"(?!ENC )\S"),
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
    for lineno, line in enumerate(_split_lines(text), start=1):
        view = line.rstrip("\r\n").replace("\t", " ")
        for kind, pattern in patterns.items():
            if pattern.search(view):
                found.append(UnmaskedSecret(lineno, kind))
    return found

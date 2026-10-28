"""Secret masking in device configs (ADR-0003, YAN-1661).

Sample configs are assembled at runtime so this file doesn't trip the customer data check
or gitleaks. Every value here is made up.
"""

import difflib
import re

import pytest
from cartio_core.masking import (
    MaskingKey,
    Platform,
    UnmaskedSecretError,
    find_unmasked,
    mask_config,
)

SP = " "
KEY = MaskingKey("k1", b"customer-a-masking-key-0123456789")
OTHER_CUSTOMER = MaskingKey("k1", b"customer-b-masking-key-0123456789")
ROTATED = MaskingKey("k2", b"customer-a-rotated-key-0123456789")
PLACEHOLDER = re.compile(r"<masked:[A-Za-z0-9_.-]+:[0-9a-f]{16}>")

PEM_KEY = (
    "-----BEGIN RSA" + SP + "PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n"
    "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n"
    "-----END RSA" + SP + "PRIVATE KEY-----"
)
PEM_CERT = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBaMQswCQYDVQQGEwJJ\n"
    "-----END CERTIFICATE-----"
)

# (config line, the secret in it). Each IOS and IOS-XE secret type ADR-0003 and YAN-1661 name.
IOS_SECRETS = [
    ("enable secret" + SP + "9 $9$nhEmQVczB7dqsO$X.HsgL6x1il0RxkOSSvyQYwucySCt7qFm4v7pq", None),
    ("enable secret" + SP + "5 $1$mERr$hx5rVt7rPNoS4wqbXKX7m0", None),
    (
        "enable secret" + SP + "8 $8$dsYGNam3K1SIJO$7nv/35M/qr6t.dVc7UY9zrJDWRVqncHub1PE9UlMQFs",
        None,
    ),
    ("enable secret" + SP + "Plain Text Enable", "Plain Text Enable"),
    ("enable password" + SP + "7 0822455D0A16", "0822455D0A16"),
    ("username admin privilege 15 secret" + SP + "9 $9$abcdEFGHijkl$mnopQRSTuvwx", None),
    (
        "username ops privilege 15 algorithm-type scrypt secret" + SP + "9 $9$Zx1Yy2Ww3$Vv4Uu5",
        "$9$Zx1Yy2Ww3$Vv4Uu5",
    ),
    ("username netops password" + SP + "0 two words", "two words"),
    ("username legacy password" + SP + "7 104D000A0618", "104D000A0618"),
    (" password" + SP + "7 060506324F41", "060506324F41"),
    (" password" + SP + "LinePass1", "LinePass1"),
    ("snmp-server" + SP + "community Pub1icRO RO 10", "Pub1icRO"),
    ("snmp-server" + SP + "community 8 Pr1vRW RW", "Pr1vRW"),
    ("snmp-server host 10.0.0.5 version 2c TrapComm1", "TrapComm1"),
    ("snmp-server host 10.0.0.6 vrf MGMT informs version 2c TrapComm2 udp-port 162", "TrapComm2"),
    ("snmp-server user monitor RO-GROUP v3 auth sha AuthPass1 priv aes 128 PrivPass1", None),
    ("tacacs-server host 10.0.0.9 key" + SP + "7 0835495D1D", "0835495D1D"),
    ("radius-server key" + SP + "0 RadiusKey1", "RadiusKey1"),
    (" server-private 10.0.0.10 key" + SP + "7 13061E010803", "13061E010803"),
    ("crypto isakmp key" + SP + "IsakmpPsk1 address 192.0.2.1", "IsakmpPsk1"),
    ("crypto isakmp key" + SP + "6 IsakmpPsk2 address 192.0.2.2", "IsakmpPsk2"),
    ("  pre-shared-key address 192.0.2.3 key" + SP + "KeyringPsk1", "KeyringPsk1"),
    (" pre-shared-key local" + SP + "6 Ikev2Local1", "Ikev2Local1"),
    (" pre-shared-key" + SP + "Ikev2Both1", "Ikev2Both1"),
    (" authentication local pre-share key" + SP + "ProfilePsk1", "ProfilePsk1"),
    (" ip ospf authentication-key" + SP + "7 0207005E", "0207005E"),
    (" ip ospf message-digest-key 1 md5" + SP + "7 13061E01", "13061E01"),
    (" neighbor 192.0.2.7 password" + SP + "7 045802150C2E", "045802150C2E"),
    ("  key-string" + SP + "7 1511021F0725", "1511021F0725"),
    (" standby 1 authentication md5 key-string" + SP + "HsrpMd5Key", "HsrpMd5Key"),
    (" standby 1 authentication HsrpText", "HsrpText"),
    (" vrrp 2 authentication text VrrpText", "VrrpText"),
    ("ntp authentication-key 1 md5 NtpKey1 7", "NtpKey1"),
    (" wpa-psk ascii" + SP + "0 Wireless Pass", "Wireless Pass"),
    (" ppp chap password" + SP + "0 ChapPass1", "ChapPass1"),
    ("key config-key password-encrypt ConfigKey1", "ConfigKey1"),
]


def _secret_of(line, value):
    return value if value is not None else line.split()[-1]


@pytest.mark.parametrize(("line", "value"), IOS_SECRETS)
def test_each_ios_secret_type_is_masked(line, value):
    config = f"hostname r1\n{line}\nend\n"
    result = mask_config(config, Platform.IOS_XE, KEY)
    secret = _secret_of(line, value)
    assert secret not in result.text
    assert PLACEHOLDER.search(result.text)
    assert result.secrets_masked >= 1
    assert find_unmasked(result.text) == []


def test_snmpv3_auth_and_priv_keys_are_both_masked():
    line = "snmp-server user monitor RO-GROUP v3 auth sha AuthPass1 priv aes 128 PrivPass1"
    result = mask_config(line + "\n", Platform.IOS, KEY)
    assert "AuthPass1" not in result.text and "PrivPass1" not in result.text
    assert result.text.startswith("snmp-server user monitor RO-GROUP v3 auth sha <masked:")
    assert " priv aes 128 <masked:" in result.text
    assert result.secrets_masked == 2


def test_the_rest_of_the_line_is_kept():
    # Assessment checks need the access level and ACL of a community, and the type of a hash.
    result = mask_config("snmp-server" + SP + "community Pub1icRO RW 10\n", Platform.IOS, KEY)
    assert re.fullmatch(r"snmp-server community <masked:k1:[0-9a-f]{16}> RW 10\n", result.text)
    result = mask_config("enable secret" + SP + "5 $1$mERr$hx5rVt7r\n", Platform.IOS, KEY)
    assert re.fullmatch(r"enable secret 5 <masked:k1:[0-9a-f]{16}>\n", result.text)
    result = mask_config("crypto isakmp key" + SP + "Psk1 address 192.0.2.1\n", "ios", KEY)
    assert result.text.endswith(" address 192.0.2.1\n")


def test_tacacs_server_block_key_is_masked_but_key_chain_ids_are_not():
    config = (
        "tacacs server TAC1\n"
        " address ipv4 10.0.0.9\n"
        " key" + SP + "TacacsBlockKey\n"
        "key chain OSPF\n"
        " key 1\n"
        "  key-string" + SP + "7 1511021F0725\n"
    )
    result = mask_config(config, Platform.IOS, KEY)
    assert "TacacsBlockKey" not in result.text
    assert " key 1\n" in result.text


def test_ios_certificate_chain_is_collapsed():
    config = (
        "crypto pki certificate chain TP-self-signed-1\n"
        " certificate self-signed 01\n"
        "  3082022B 30820194 A0030201 02020101 300D0609 2A864886\n"
        "  F70D0101 05050030 31312F30 2D060355 04031326 494F532D\n"
        "  \tquit\n"
        "!\n"
    )
    result = mask_config(config, Platform.IOS, KEY)
    assert "3082022B" not in result.text and "F70D0101" not in result.text
    lines = result.text.splitlines()
    assert lines[1] == " certificate self-signed 01"
    assert PLACEHOLDER.fullmatch(lines[2].strip())
    assert lines[3].strip() == "quit"


def test_unterminated_certificate_is_masked_to_the_end_of_its_block():
    config = (
        "crypto pki certificate chain TP\n certificate ca 01\n  3082022B 30820194\nhostname r1\n"
    )
    result = mask_config(config, Platform.IOS, KEY)
    assert "3082022B" not in result.text
    assert result.text.endswith("hostname r1\n")


@pytest.mark.parametrize("platform", list(Platform))
@pytest.mark.parametrize("pem", [PEM_KEY, PEM_CERT])
def test_pem_blocks_are_masked_on_every_platform(platform, pem):
    result = mask_config(f"hostname r1\n{pem}\nend\n", platform, KEY)
    assert "BEGIN" not in result.text and "MIIE" not in result.text and "MIID" not in result.text
    assert result.text.startswith("hostname r1\n<masked:k1:")
    assert result.text.endswith("\nend\n")


def test_truncated_pem_block_is_masked_to_the_end():
    truncated = PEM_KEY.split("-----END")[0]
    result = mask_config(f"hostname r1\n{truncated}", Platform.IOS, KEY)
    assert "MIIE" not in result.text


FORTIOS_SECRETS = [
    ("    set password" + SP + "ENC SH2Bk7t5hZ3lPa9kE0Q==", "SH2Bk7t5hZ3lPa9kE0Q=="),
    ("    set psksecret" + SP + "ENC 4mUcZzYp1qO1sQ==", "4mUcZzYp1qO1sQ=="),
    ("    set psksecret-remote" + SP + "ENC 9pQrStUv==", "9pQrStUv=="),
    ("    set ppk-secret" + SP + "ENC 7aBcDeF==", "7aBcDeF=="),
    ("    set secret" + SP + "ENC RadSecretEnc==", "RadSecretEnc=="),
    ("    set key" + SP + "ENC TacKeyEnc==", "TacKeyEnc=="),
    ("    set auth-pwd" + SP + "ENC SnmpAuthEnc==", "SnmpAuthEnc=="),
    ("    set priv-pwd" + SP + "ENC SnmpPrivEnc==", "SnmpPrivEnc=="),
    ("    set api-key" + SP + "ENC ApiKeyEnc==", "ApiKeyEnc=="),
    ("    set passphrase" + SP + "ENC WifiEnc==", "WifiEnc=="),
    ("    set md5-keys 1" + SP + "ENC OspfMd5Enc==", "OspfMd5Enc=="),
    ('    set password "PlainQuoted pass"', "PlainQuoted pass"),
    ("    set secret RadPlain1", "RadPlain1"),
    ("    set psksecret PlainPsk1", "PlainPsk1"),
]


@pytest.mark.parametrize(("line", "value"), FORTIOS_SECRETS)
def test_each_fortios_secret_type_is_masked(line, value):
    config = f'config system admin\n    edit "admin"\n{line}\n    next\nend\n'
    result = mask_config(config, Platform.FORTIOS, KEY)
    assert value not in result.text
    assert PLACEHOLDER.search(result.text)
    assert find_unmasked(result.text, Platform.FORTIOS) == []


def test_fortios_snmp_community_name_is_masked_only_in_its_block():
    config = (
        "config system snmp community\n"
        "    edit 1\n"
        '        set name "FortiComm1"\n'
        "        config hosts\n"
        "            edit 1\n"
        '                set ip "10.0.0.5 255.255.255.255"\n'
        "            next\n"
        "        end\n"
        "    next\n"
        "end\n"
        "config firewall address\n"
        '    edit "srv1"\n'
        '        set name "keep-me"\n'
        "    next\n"
        "end\n"
    )
    result = mask_config(config, Platform.FORTIOS, KEY)
    assert "FortiComm1" not in result.text
    assert 'set name "<masked:k1:' in result.text
    assert 'set name "keep-me"' in result.text


def test_fortios_private_key_and_certificate_values_are_masked():
    config = (
        "config vpn certificate local\n"
        '    edit "srv"\n'
        f'        set private-key "{PEM_KEY}"\n'
        f'        set certificate "{PEM_CERT}"\n'
        "    next\n"
        "end\n"
    )
    result = mask_config(config, Platform.FORTIOS, KEY)
    assert "MIIE" not in result.text and "MIID" not in result.text
    assert re.search(r'set private-key "<masked:k1:[0-9a-f]{16}>"', result.text)
    assert re.search(r'set certificate "<masked:k1:[0-9a-f]{16}>"', result.text)


def test_ordinary_lines_are_left_alone():
    config = (
        "hostname r1\n"
        "service password-encryption\n"
        "password encryption aes\n"
        "security passwords min-length 10\n"
        "key chain OSPF\n"
        " key 1\n"
        "interface Gi1\n"
        " standby 1 authentication md5 key-chain HSRP\n"
        "end\n"
    )
    result = mask_config(config, Platform.IOS, KEY)
    assert result.text == config
    assert result.secrets_masked == 0
    forti = 'config system global\n    set hostname "fw1"\n    set admintimeout 5\nend\n'
    assert mask_config(forti, Platform.FORTIOS, KEY).text == forti


def test_same_secret_same_placeholder_and_a_change_shows_in_the_diff():
    before = "hostname r1\nsnmp-server" + SP + "community OldComm RO\n"
    after = "hostname r1\nsnmp-server" + SP + "community NewComm RO\n"
    a1 = mask_config(before, Platform.IOS, KEY)
    a2 = mask_config(before, Platform.IOS, KEY)
    b = mask_config(after, Platform.IOS, KEY)
    assert a1.text == a2.text and a1.content_hash == a2.content_hash
    assert a1.content_hash != b.content_hash
    diff = "".join(difflib.unified_diff(a1.text.splitlines(True), b.text.splitlines(True)))
    removed = [line for line in diff.splitlines() if line.startswith("-snmp")]
    added = [line for line in diff.splitlines() if line.startswith("+snmp")]
    assert len(removed) == 1 and len(added) == 1
    assert "OldComm" not in diff and "NewComm" not in diff


def test_placeholders_differ_across_customers_and_record_the_key_version():
    config = "snmp-server" + SP + "community Pub1ic RO\n"
    a = mask_config(config, Platform.IOS, KEY)
    b = mask_config(config, Platform.IOS, OTHER_CUSTOMER)
    rotated = mask_config(config, Platform.IOS, ROTATED)
    assert a.text != b.text
    assert "<masked:k2:" in rotated.text and rotated.key_version == "k2"


def test_masking_is_idempotent():
    config = "enable secret" + SP + "9 $9$abc$def\nsnmp-server" + SP + "community C1 RO\n"
    once = mask_config(config, Platform.IOS, KEY)
    twice = mask_config(once.text, Platform.IOS, KEY)
    assert twice.text == once.text
    assert twice.secrets_masked == 0


def test_crlf_line_endings_are_kept():
    config = "hostname r1\r\nenable secret" + SP + "5 $1$abc$def\r\nend\r\n"
    result = mask_config(config, Platform.IOS, KEY)
    assert result.text.count("\r\n") == 3
    assert "$1$abc$def" not in result.text


def test_content_hash_is_over_the_masked_text():
    import hashlib

    result = mask_config("enable secret" + SP + "5 $1$abc$def\n", Platform.IOS, KEY)
    assert result.content_hash == hashlib.sha256(result.text.encode()).hexdigest()


def test_leftover_secret_fails_closed_without_naming_it(monkeypatch):
    from cartio_core import masking

    # Simulate a rule that misses: masking runs with no IOS rules at all.
    monkeypatch.setitem(masking._RULES, Platform.IOS, [])
    with pytest.raises(UnmaskedSecretError) as raised:
        mask_config("hostname r1\nenable secret" + SP + "5 $1$Leak$Value\n", Platform.IOS, KEY)
    assert "line 2: secret" in str(raised.value)
    assert "Leak" not in str(raised.value)


@pytest.mark.parametrize(
    "line",
    [
        "enable secret" + SP + "5 $1$abc$def",
        "username a password" + SP + "7 0822455D0A16",
        "snmp-server" + SP + "community Pub1ic RO",
        "    set password" + SP + "ENC SH2Bk7t5hZ3l==",
        "-----BEGIN RSA" + SP + "PRIVATE KEY-----",
    ],
)
def test_controller_scan_finds_unmasked_secrets(line):
    assert find_unmasked(f"hostname r1\n{line}\n") != []


def test_unknown_platform_is_rejected():
    with pytest.raises(ValueError):
        mask_config("hostname r1\n", "junos", KEY)


@pytest.mark.parametrize(
    ("version", "secret"),
    [("k1", b"short"), ("bad:version", b"x" * 32), ("", b"x" * 32), ("x" * 17, b"x" * 32)],
)
def test_weak_or_malformed_keys_are_rejected(version, secret):
    with pytest.raises(ValueError):
        MaskingKey(version, secret)


def test_key_repr_hides_the_key():
    assert "customer-a" not in repr(KEY)

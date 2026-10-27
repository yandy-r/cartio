"""Block customer device data from being committed.

No real configs, outputs or credentials go in the repo (03 Getting started, ADR-0003).
This catches the common shapes: raw config files by name, and unmasked device secrets by
content. Run by pre-commit on staged files, and by CI on every tracked file.

    python scripts/check_customer_data.py [FILE ...]

With no files, it checks everything git tracks. Masked values (<masked>, ****) pass.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

# A secret's value is allowed when it has been masked.
_UNMASKED = r"(?!<|\*{3,}|MASKED\b)\S+"

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "Cisco type 7 password": re.compile(r"\bpassword 7 [0-9A-Fa-f]{4,}"),
    "Cisco secret": re.compile(r"\b(?:enable )?secret [0589] " + _UNMASKED),
    # Plain-text form only; the typed form (with a 0, 5, 8 or 9 first) is the pattern above.
    "Cisco enable secret": re.compile(r"\benable secret (?![0589] )" + _UNMASKED),
    "SNMP community": re.compile(r"\bsnmp-server community " + _UNMASKED),
    "FortiOS encrypted secret": re.compile(
        r"\bset (?:password|passwd|psksecret|private-key|key|auth-pwd|priv-pwd) ENC " + _UNMASKED
    ),
    "private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
}

# Raw device files by name. Sanitized fixtures still get the content checks above.
BLOCKED_NAMES = ["*running-config*", "*startup-config*", "*show-run*", "*show_run*"]
ALLOWED_DIRS = ("tests/fixtures/sanitized/",)


def check_name(path: str) -> str | None:
    posix = Path(path).as_posix()
    if posix.startswith(ALLOWED_DIRS):
        return None
    name = Path(posix).name.lower()
    for pattern in BLOCKED_NAMES:
        if fnmatch.fnmatch(name, pattern):
            return f"looks like a raw device file ({pattern})"
    return None


def check_text(text: str) -> list[tuple[int, str]]:
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                found.append((lineno, label))
    return found


def check_file(path: str) -> list[str]:
    problems = []
    if reason := check_name(path):
        problems.append(f"{path}: {reason}")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return problems
    problems += [f"{path}:{lineno}: unmasked {label}" for lineno, label in check_text(text)]
    return problems


def tracked_files() -> list[str]:
    out = subprocess.run(  # noqa: S603
        ["git", "ls-files"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.splitlines()


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv else tracked_files()
    problems = [p for path in paths for p in check_file(path)]
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(
            "\nCustomer device data must not be committed. Mask the secrets, or use a "
            "sanitized fixture under tests/fixtures/sanitized/.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

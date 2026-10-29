"""The cartio-collector command."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
from cartio_core.protocol import MAX_WAIT

from . import __version__
from .client import ControllerError, Session, enroll
from .config import CollectorConfig, ConfigError, default_path
from .jobs import Journal, Worker

_INSPECTED = (
    "warning: the controller's TLS key isn't the pinned one, so something on the path inspects "
    "TLS. Reported to the controller."
)


def _cmd_enroll(args: argparse.Namespace) -> int:
    # Saved with the identity, so make it absolute: check-ins may run from another directory.
    ca_file = str(Path(args.ca_file).resolve()) if args.ca_file else ""
    config = enroll(args.controller, args.token, proxy=args.proxy, ca_file=ca_file)
    config.save(args.config)
    print(f"Enrolled as collector {config.collector_id} for {config.customer}.")
    print(f"Identity and private key saved to {args.config}")
    return 0


def _cmd_checkin(args: argparse.Namespace) -> int:
    config = CollectorConfig.load(args.config)
    reported_inspection = False
    with Session(config) as session:
        while True:
            try:
                result = session.check_in()
            except httpx.TransportError as exc:
                # A network blip shouldn't stop a running collector; a refusal still does.
                if args.once:
                    raise
                print(f"warning: check-in failed, retrying: {exc}", file=sys.stderr)
            else:
                if session.inspected and not reported_inspection:
                    print(_INSPECTED, file=sys.stderr)
                reported_inspection = session.inspected
                print(f"Checked in: {result.status}.")
                if args.once:
                    return 0
            time.sleep(args.interval)


def _cmd_run(args: argparse.Namespace) -> int:
    """Check in every `interval` seconds and, in between, wait for jobs and run them."""
    config = CollectorConfig.load(args.config)
    journal = Journal(config.journal_path(args.config))
    failures = 0
    last_check_in = None
    reported_inspection = False
    with Session(config) as session:
        worker = Worker(session, journal)
        while True:
            try:
                if last_check_in is None or time.monotonic() - last_check_in >= args.interval:
                    session.check_in()
                    last_check_in = time.monotonic()
                    if session.inspected and not reported_inspection:
                        print(_INSPECTED, file=sys.stderr)
                    reported_inspection = session.inspected
                ran = worker.poll_once(0 if args.once else MAX_WAIT)
                if ran:
                    print(f"Ran {ran} job(s).")
                failures = 0
            except httpx.TransportError as exc:
                if args.once:
                    raise
                # A network blip shouldn't stop a running collector; a refusal still does.
                failures += 1
                print(f"warning: controller unreachable, retrying: {exc}", file=sys.stderr)
                time.sleep(min(60, 2**failures))
            if args.once:
                return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cartio-collector", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", type=Path, default=default_path(), help="Where the collector identity is kept."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser("enroll", help="Enroll with the controller using a one-time token.")
    p_enroll.add_argument("--controller", required=True, help="Controller URL, e.g. https://...")
    p_enroll.add_argument("--token", required=True, help="One-time enrollment token.")
    p_enroll.add_argument(
        "--proxy",
        default="",
        help="Proxy URL, e.g. http://user:pass@proxy:3128. "
        "Default: the HTTPS_PROXY environment variable.",
    )
    p_enroll.add_argument(
        "--ca-file", default="", help="CA bundle to trust instead of the OS certificate store."
    )
    p_enroll.set_defaults(func=_cmd_enroll)

    p_checkin = sub.add_parser("checkin", help="Check in with the controller.")
    p_checkin.add_argument("--once", action="store_true", help="Check in once and exit.")
    p_checkin.add_argument("--interval", type=int, default=60, help="Seconds between check-ins.")
    p_checkin.set_defaults(func=_cmd_checkin)

    p_run = sub.add_parser("run", help="Check in, wait for jobs from the controller and run them.")
    p_run.add_argument("--once", action="store_true", help="Run the jobs waiting now and exit.")
    p_run.add_argument("--interval", type=int, default=60, help="Seconds between check-ins.")
    p_run.set_defaults(func=_cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ControllerError, ConfigError, httpx.HTTPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""The cartio-collector command."""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import sys
import time
from pathlib import Path

import httpx
from cartio_core.protocol import COLLECT_PLATFORMS, MAX_WAIT

from . import __version__
from .client import ControllerError, Session, enroll
from .config import CollectorConfig, ConfigError, default_path
from .jobs import Journal, OnSite, Worker
from .site import SECRET_FIELDS, Site, SiteError
from .store import StoreError, open_store

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
    on_site = None
    if config.store:
        on_site = OnSite(Site.for_config(args.config), open_store(config.store))
    failures = 0
    last_check_in = None
    reported_inspection = False
    with Session(config) as session:
        worker = Worker(session, journal, on_site)
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


def _cmd_store_set(args: argparse.Namespace) -> int:
    config = CollectorConfig.load(args.config)
    if args.folder:
        settings = {"kind": "folder", "path": str(Path(args.folder).resolve())}
    else:
        settings = {"kind": "postgres", "dsn": args.postgres}
    store = open_store(settings)
    # Only a store that works is saved.
    store.check()
    dataclasses.replace(config, store=settings).save(args.config)
    print(f"On-site store set: {store.kind}. Collected data is written there, not uploaded.")
    return 0


def _cmd_store_check(args: argparse.Namespace) -> int:
    config = CollectorConfig.load(args.config)
    store = open_store(config.store)
    store.check()
    print(f"The on-site {store.kind} store can be written and read back.")
    return 0


def _cmd_credential_add(args: argparse.Namespace) -> int:
    site = Site.for_config(args.config)
    required, optional = SECRET_FIELDS[args.kind]
    secret = {}
    # Typed at a prompt, never on the command line, where it would be kept in shell history.
    for name in required + optional:
        if name == "private_key":
            path = getpass.getpass("Path to the private key file: ")
            secret[name] = Path(path).read_text(encoding="utf-8")
        else:
            label = name.replace("_", " ")
            secret[name] = getpass.getpass(
                f"{label.capitalize()}: "
                if name in required
                else f"{label.capitalize()} (empty for none): "
            )
    replaced = site.set_credential(args.name, args.kind, args.username, secret)
    print(f"Credential {args.name!r} {'replaced' if replaced else 'added'}. It stays on this host.")
    return 0


def _cmd_credential_list(args: argparse.Namespace) -> int:
    for credential in Site.for_config(args.config).credential_list():
        print(f"{credential['name']}\t{credential['kind']}\t{credential['username']}")
    return 0


def _cmd_credential_remove(args: argparse.Namespace) -> int:
    Site.for_config(args.config).remove_credential(args.name)
    print(f"Credential {args.name!r} removed.")
    return 0


def _cmd_target_add(args: argparse.Namespace) -> int:
    Site.for_config(args.config).add_target(args.host, args.platform, args.credential, args.port)
    print(f"Target {args.host} set.")
    return 0


def _cmd_target_list(args: argparse.Namespace) -> int:
    for target in Site.for_config(args.config).targets():
        print(f"{target['host']}\t{target['platform']}\t{target['port']}\t{target['credential']}")
    return 0


def _cmd_target_remove(args: argparse.Namespace) -> int:
    Site.for_config(args.config).remove_target(args.host)
    print(f"Target {args.host} removed.")
    return 0


def _add_on_site_commands(sub) -> None:
    """Commands for an On site customer's collector, which keeps data and secrets on site."""
    p_store = sub.add_parser("store", help="Set up the on-site store (On site customers).")
    store = p_store.add_subparsers(dest="store_command", required=True)
    p_set = store.add_parser("set", help="Choose the on-site store and check it can be written.")
    where = p_set.add_mutually_exclusive_group(required=True)
    where.add_argument("--folder", help="A folder on a mounted SMB or NFS share.")
    where.add_argument(
        "--postgres",
        help="A PostgreSQL connection string. Keep the password in PGPASSWORD or ~/.pgpass.",
    )
    p_set.set_defaults(func=_cmd_store_set)
    store.add_parser("check", help="Check the on-site store.").set_defaults(func=_cmd_store_check)

    p_cred = sub.add_parser("credential", help="Device credentials kept on this collector.")
    cred = p_cred.add_subparsers(dest="credential_command", required=True)
    p_add = cred.add_parser("add", help="Add or replace a credential. Secrets are prompted for.")
    p_add.add_argument("name")
    p_add.add_argument("--kind", choices=sorted(SECRET_FIELDS), default="ssh_password")
    p_add.add_argument("--username", required=True)
    p_add.set_defaults(func=_cmd_credential_add)
    cred.add_parser("list", help="Names and kinds, never secrets.").set_defaults(
        func=_cmd_credential_list
    )
    p_rm = cred.add_parser("remove", help="Remove a credential.")
    p_rm.add_argument("name")
    p_rm.set_defaults(func=_cmd_credential_remove)

    p_target = sub.add_parser("target", help="Devices this collector collects (On site).")
    target = p_target.add_subparsers(dest="target_command", required=True)
    p_tadd = target.add_parser("add", help="Add a device, or change one with this host.")
    p_tadd.add_argument("host")
    p_tadd.add_argument("--platform", required=True, choices=COLLECT_PLATFORMS)
    p_tadd.add_argument("--credential", required=True, help="The name of a credential.")
    p_tadd.add_argument("--port", type=int, default=22)
    p_tadd.set_defaults(func=_cmd_target_add)
    target.add_parser("list", help="List the devices.").set_defaults(func=_cmd_target_list)
    p_trm = target.add_parser("remove", help="Remove a device.")
    p_trm.add_argument("host")
    p_trm.set_defaults(func=_cmd_target_remove)


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

    _add_on_site_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        ControllerError,
        ConfigError,
        SiteError,
        StoreError,
        httpx.HTTPError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

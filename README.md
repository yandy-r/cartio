# Cartio

Cartio is IT Cloud Network's in-house tool for discovering and assessing customer networks.
Collectors run inside each customer network and dial out to one central controller, so an
engineer connects once and sees every customer. Version 1 is read-only.

Planning, ADRs and issues live in the Linear project "Cartio v1". The ADRs are also copied
into [docs/adr](docs/adr/README.md). Branches, target releases and backports follow
[RELEASING.md](RELEASING.md). What we send a customer before an assessment is the
[customer onboarding checklist](docs/onboarding/customer-checklist.md). The stack is Python for both the collector and the
controller (ADR-0006).

The engineer UI is built from the B1 design in [docs/design](docs/design/README.md)
(ADR-0007): [DESIGN.md](docs/design/DESIGN.md) is the spec for every screen. Agents working in
this repo get the same pointer from [CLAUDE.md](CLAUDE.md).

`.claude/settings.json` enables the `ycc` Claude Code plugin from
[yandy-r/ystack](https://github.com/yandy-r/ystack) for local sessions, which gives skills such as
`/ycc:release-model` and `/ycc:backport`. Cloud sessions ignore plugins declared there. For
cloud threads, the project's cloud environment installs the plugin in its setup script with
`claude plugin marketplace add yandy-r/ystack && claude plugin install ycc@ycc --scope user`.

## Layout

| Path | What it is |
| -- | -- |
| `packages/cartio-core` | Code shared by both sides: the collector-controller protocol and secret masking; later parsers and checks. |
| `packages/cartio-collector` | The collector. Runs on a customer VM, or an engineer's laptop as the fallback. Always connects out; never listens. |
| `packages/cartio-controller` | The controller. Django with PostgreSQL. |
| `tests` | Tests for all three, including an end-to-end collector check-in. |
| `docs/design` | The engineer UI design: the spec and the prototype's source (ADR-0007). |

## Get started

Install [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync
uv run pytest
```

Then install the commit hooks, which block credentials and customer device data:

```sh
uv tool install pre-commit
pre-commit install
```

Run a controller and a collector locally:

```sh
export CARTIO_DEBUG=1
uv run cartio-controller migrate
uv run cartio-controller createsuperuser
uv run cartio-controller enroll_engineer <username>
uv run cartio-controller create_collector --customer "Lab" --name "lab-vm"
uv run cartio-controller runserver
```

In a second terminal, use the token the `create_collector` command printed:

```sh
uv run cartio-collector enroll --controller http://127.0.0.1:8000 --token <token>
uv run cartio-collector checkin --once
```

Sign in at http://127.0.0.1:8000/ with the superuser's password. The first sign-in asks for the
enrollment code `enroll_engineer` printed, then shows a key to add to an authenticator app; every
sign-in after that needs a code from the app. The customers list shows each collector's health
(online when it checked in within the last 3 minutes, last check-in, version), and the admin at
http://127.0.0.1:8000/admin/ uses the same sign-in. Sessions end after `CARTIO_SESSION_HOURS`
(8 by default) or when the browser closes. Engineers are staff accounts; run
`enroll_engineer --reset <username>` to replace a lost authenticator.

## Try it with Docker Compose

[compose.yaml](compose.yaml) runs PostgreSQL and the controller with the sanitized lab loaded, so
every screen has data. It's for local testing only: development settings, plain http on
127.0.0.1 and default passwords (override them with `CARTIO_APP_PASSWORD`,
`CARTIO_OWNER_PASSWORD`, `POSTGRES_PASSWORD` and `CARTIO_ENGINEER_PASSWORD`).

```sh
docker compose up -d --build
docker compose exec controller cartio-controller enroll_engineer engineer
```

Startup follows the [database roles](docs/deploy/database.md): `db` creates `cartio_owner` and
`cartio_app`, `migrate` runs as the owner, and `seed` and the controller run as the app role. The
master key is generated into the `keys` volume on first start. `seed` creates the staff account
`engineer` (password `cartio-local`) and runs `load_lab`, which stores the fixtures in
`tests/fixtures/sanitized` as uploads for a customer called Lab and runs the checks. It makes
two engagements: Q2 review (completed; core-sw1, and edge-fw1 on its weak config) and Q4 review
(active; all three devices, edge-fw1 hardened). `load_lab` only runs with `CARTIO_DEBUG=1`.

Sign in at http://127.0.0.1:8000/ as `engineer` with the enrollment code, then add the key to an
authenticator app. Things to try:

- All customers, then Lab: the Site map (HQ and Floor 2, L2 and L3), Inventory with filters and
  CSV/XLSX export, and Findings from the check results (edit, add a note, dismiss).
- Compare Q4 review with Q2 review: floor-sw1 is new and edge-fw1's config changed. Add the
  changes to the report, then preview it and export PDF or DOCX.
- New customer: the onboarding wizard. The Audit log shows everything above.

A collector can enroll and check in, so the Collectors screen shows it online. It gets no jobs
yet (the job channel is YAN-1651), so it collects nothing:

```sh
token=$(docker compose exec -T controller cartio-controller create_collector --customer Lab --name lab-vm | tail -1)
CARTIO_ENROLL_TOKEN=$token docker compose --profile collector up -d collector
```

`docker compose down -v` removes everything, including the database and the master key.

## Engineer API

Customers, engagements and sites are managed through a JSON API under `/api/v1/`. It accepts a
signed-in staff session (sign in at `/sign-in`, with the authenticator code), writes need the user's model permissions for that record type, and writes need the CSRF token.

| Path | What it does |
| -- | -- |
| `customers` | List (add `?archived=true` to include archived) and create. `data_mode` is `hosted` (default) or `on_site` (ADR-0002). `live_view_allowed` (default false) lets engineers see an On site customer through the controller (ADR-0005). Neither can be changed after creation. |
| `customers/<id>` | Read and update. |
| `customers/<id>/engagements`, `customers/<id>/sites` | List and create, always within that customer. A site's `seed_hints` are IP addresses, networks no wider than /16 (IPv4) or /48 (IPv6), or host names. |
| `.../<id>/archive`, `.../<id>/restore` | Archive or restore any of the above. Archived records, and everything under an archived customer, are read-only. Nothing is deleted, through the API or the admin. |

The collector only accepts plain `http` for a controller on the same machine. Any other controller URL must use `https`.

## Configuration

The controller reads its settings from the environment:

| Variable | Meaning |
| -- | -- |
| `CARTIO_SECRET_KEY` | Django secret key. Required unless `CARTIO_DEBUG=1`. |
| `CARTIO_DEBUG` | `1` for local development only. |
| `CARTIO_ALLOWED_HOSTS` | Comma-separated host names the controller answers to. |
| `DATABASE_URL` | PostgreSQL URL. Defaults to a local SQLite file for development. The controller connects as the app role, and `migrate` as the owner role ([database roles](docs/deploy/database.md)). |
| `CARTIO_DB_APP_ROLE` | The PostgreSQL role the controller runs as. `migrate`, run as the owner, grants it row access only: no `TRUNCATE`, no table changes, nothing that rewrites the audit log. |
| `CARTIO_MASTER_KEY_FILE` | File holding the base64 32-byte master key that wraps every credential key. Back it up on its own ([master key](docs/deploy/master-key.md)). `CARTIO_MASTER_KEY` can hold the key instead. |
| `CARTIO_PREVIOUS_MASTER_KEY_FILE` | Set only while `rotate_master_key` replaces the master key: the old key, used to open what it wrapped. `CARTIO_PREVIOUS_MASTER_KEY` can hold it instead. |
| `CARTIO_SESSION_HOURS` | How long an engineer stays signed in. Defaults to 8. Sessions also end when the browser closes. |
| `CARTIO_DATA_DIR` | Where the SQLite file and collected static files go. Defaults to the current directory. |

## Secret masking

Configs are masked on the collector, before they leave it (ADR-0003), by
`cartio_core.masking.mask_config(text, platform, key)` for `ios`, `ios-xe` and `fortios`. Each
secret becomes `<masked:<key version>:<digest>>`, where the digest is a keyed HMAC-SHA256 under a
per-customer key, so a changed secret shows up in a diff without its value. The content hash is
taken over the masked text. If anything that looks like a secret survives masking, it raises
`UnmaskedSecretError` instead of returning the config; `find_unmasked` runs the same scan on
configs the controller receives.

SNMPv1/v2c communities are also judged on the collector, since the controller never sees their
values. One that is shorter than 8 characters or on a list of well-known defaults (public,
private, cisco...) gets a marked placeholder, `<masked:<key version>:<digest>:weak>`, and the
insecure-management check fails on it (YAN-1744).

To support a new platform, add its rules to `_RULES` in `masking.py` and a test for every secret
type it can hold in `tests/test_masking.py`. Build test configs at runtime, as that file does, so
the commit hooks don't flag them.

## Assessment checks

Checks are code in `cartio_core.checks` (YAN-1664). Each one looks at a device, with the rest of
its engagement for context, and returns pass, fail, not applicable or unknown, with evidence.
Missing data is always unknown, never pass. To run them on an engagement's stored inventory,
configs and outputs, without collecting again:

```sh
uv run cartio-controller run_checks --customer "Lab" --engagement "Audit" --user <username>
```

Each run adds an `AssessmentRun` with one `CheckResult` per device and check, and an
`assessment.run` audit event. Results are append-only; the latest run is the current one. On site
customers are refused, because their results belong in their own store. To add a check, write
its function and add a `Check` to `CHECKS`; never reuse or rename a check id.

## Not done yet

- Collector authentication is a placeholder: a one-time enrollment token is exchanged for a
  bearer secret. Per-collector mTLS certificates replace it (ADR-0001, YAN-1640).
- Check-in returns no jobs yet. Discovery and import arrive in M2.
- Masking is not wired into collection yet: the collector has no drivers or masking key until
  discovery (YAN-1656) and credential delivery (YAN-1653) land.
- Packaging (container image for the VM, Windows build for the laptop) is YAN-1641.

## Rules from day one

- Drivers are read-only in v1 (ADR-0004). A non-read command needs an ADR.
- No customer data in the repo: no real configs, outputs, IPs, hostnames or credentials in code,
  tests or issues. gitleaks and `scripts/check_customer_data.py` enforce the common cases, in
  pre-commit and in CI. Sanitized test fixtures go under `tests/fixtures/sanitized/`.
- Secrets are masked at the collector before anything leaves it (ADR-0003).
- Every query and endpoint is scoped to one customer, with tests that try to cross customers.
- Prefer MIT, BSD or Apache licensed dependencies, especially in the collector.

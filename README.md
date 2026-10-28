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

## Engineer API

Customers, engagements and sites are managed through a JSON API under `/api/v1/`. It accepts a
signed-in staff session (sign in at `/sign-in`, with the authenticator code), writes need the user's model permissions for that record type, and writes need the CSRF token.

| Path | What it does |
| -- | -- |
| `customers` | List (add `?archived=true` to include archived) and create. `data_mode` is `hosted` (default) or `on_site` (ADR-0002) and can't be changed after creation. |
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

To support a new platform, add its rules to `_RULES` in `masking.py` and a test for every secret
type it can hold in `tests/test_masking.py`. Build test configs at runtime, as that file does, so
the commit hooks don't flag them.

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

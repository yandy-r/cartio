# Cartio

Cartio is IT Cloud Network's in-house tool for discovering and assessing customer networks.
Collectors run inside each customer network and dial out to one central controller, so an
engineer connects once and sees every customer. Version 1 is read-only.

Planning, ADRs and issues live in the Linear project "Cartio v1". The stack is Python for
both the collector and the controller (ADR-0006).

## Layout

| Path | What it is |
| -- | -- |
| `packages/cartio-core` | Code shared by both sides. Today the collector-controller protocol; later parsers, secret masking and checks. |
| `packages/cartio-collector` | The collector. Runs on a customer VM, or an engineer's laptop as the fallback. Always connects out; never listens. |
| `packages/cartio-controller` | The controller. Django with PostgreSQL. |
| `tests` | Tests for all three, including an end-to-end collector check-in. |

## Get started

Install [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync
uv run pytest
```

Run a controller and a collector locally:

```sh
export CARTIO_DEBUG=1
uv run cartio-controller migrate
uv run cartio-controller createsuperuser
uv run cartio-controller create_collector --customer "Lab" --name "lab-vm"
uv run cartio-controller runserver
```

In a second terminal, use the token the `create_collector` command printed:

```sh
uv run cartio-collector enroll --controller http://127.0.0.1:8000 --token <token>
uv run cartio-collector checkin --once
```

The check-in appears under Collectors in the admin at http://127.0.0.1:8000/admin/.

The collector only accepts plain `http` for a controller on the same machine. Any other controller URL must use `https`.

## Configuration

The controller reads its settings from the environment:

| Variable | Meaning |
| -- | -- |
| `CARTIO_SECRET_KEY` | Django secret key. Required unless `CARTIO_DEBUG=1`. |
| `CARTIO_DEBUG` | `1` for local development only. |
| `CARTIO_ALLOWED_HOSTS` | Comma-separated host names the controller answers to. |
| `DATABASE_URL` | PostgreSQL URL. Defaults to a local SQLite file for development. |
| `CARTIO_DATA_DIR` | Where the SQLite file and collected static files go. Defaults to the current directory. |

## Not done yet

- Collector authentication is a placeholder: a one-time enrollment token is exchanged for a
  bearer secret. Per-collector mTLS certificates replace it (ADR-0001, YAN-1640).
- Check-in returns no jobs yet. Discovery and import arrive in M2.
- Packaging (container image for the VM, Windows build for the laptop) is YAN-1641.

## Rules from day one

- Drivers are read-only in v1 (ADR-0004). A non-read command needs an ADR.
- No customer data in the repo: no real configs, outputs, IPs, hostnames or credentials in code,
  tests or issues.
- Secrets are masked at the collector before anything leaves it (ADR-0003).
- Every query and endpoint is scoped to one customer, with tests that try to cross customers.
- Prefer MIT, BSD or Apache licensed dependencies, especially in the collector.

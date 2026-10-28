# Working on Cartio

Read [README.md](README.md) first. It covers the layout, setup, configuration and the rules from
day one. The ADRs in [docs/adr](docs/adr/README.md) are the decisions; Linear (project
"Cartio -- v1") is where they and the issues are written.

## Engineer UI: build from the design

Any work on the engineer UI (templates, views, CSS, htmx partials, anything an engineer sees)
starts from [docs/design/DESIGN.md](docs/design/DESIGN.md) (ADR-0007).

1. Find the screen in DESIGN.md's table of screens. Read its section and the principles,
   visual system, app shell and data-mode sections. Its Linear issue has the acceptance
   criteria.
2. Open the matching file in [docs/design/prototype/](docs/design/prototype/) to see layout,
   wording, states and edge cases. It is a reference, not code to port.
3. Build it the way ADR-0006 says: Django templates extending `engineers/base.html`, htmx for
   partial updates, state on the server, customer and engagement in the URL. No front-end
   framework or build step, and no third-party requests from the page (fonts and htmx are served
   from static files).
4. Use the design's tokens, type and component styles. Don't invent new colours, spacing or
   controls when the design has one.
5. If the screen has to differ from the design, update DESIGN.md in the same PR and say why in
   the PR description. A new screen or a change to navigation or data-mode behaviour needs a
   Linear issue first.

Where they disagree: ADRs, then DESIGN.md, then the prototype.

## Rules the UI must keep

These come from the ADRs and are spelled out per screen in DESIGN.md:

* Nothing in the UI changes a customer device (ADR-0004). Commands sent from the UI go through
  the driver's read-only allow-list.
* One customer per screen, and every query scoped to it, with a test that tries to cross
  customers.
* On site customers: live view relays and stores nothing on the controller; inside access only
  shows the notice instead of data (ADR-0002, ADR-0005).
* Secrets only ever appear masked, and there is no control that reveals them (ADR-0003).
* Actions that change Cartio's own data (findings edits, exports, starting jobs, saving output,
  revoking collectors) are audit-logged.

## Before you push

* `uv run ruff format --check .`, `uv run ruff check .` and `uv run pytest`.
* No customer data in the repo: `python3 scripts/check_customer_data.py` must pass, and so must
  gitleaks in CI.

# ADR-0007 The B1 design is the reference for the engineer UI

* **Status:** Accepted (decided by Yandy, 2026-10-28). How design changes are handled is proposed.
* **Date:** 2026-10-28
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0007-the-b1-design-is-the-reference-for-the-engineer-ui-3d8336ac4347)

## Context

The engineer UI so far is the shell from YAN-1655: a top bar, a customers table and a customer
page. Every screen still to come (site map, inventory, findings, report and the rest) would
otherwise be designed ad hoc, by whoever builds it. Several people and agents build in parallel,
so screens would drift in layout, wording and behaviour, and rules from the ADRs (read-only,
data modes, masking, one customer at a time) would be applied unevenly in the UI.

A full interactive design, the B1 draft, now covers every v1 screen and the On site cases. It
was made as a prototype outside the repo.

## Decision

**Decided by Yandy:**

* The B1 design is the reference for the engineer UI. New screens and changes to existing ones
  are built from it.
* The spec is [docs/design/DESIGN.md](../design/DESIGN.md). The prototype's source is kept in
  [docs/design/prototype/](../design/prototype/), and the live prototype is linked from the spec.
* Where they disagree, the order is: ADRs, then DESIGN.md, then the prototype.

**Proposed:**

* The prototype is a picture of the result, not code. The UI is still Django templates with htmx
  (ADR-0006), with state on the server and the customer and engagement in the URL.
* A pull request that builds a screen differently from the design updates DESIGN.md in the same
  PR and says why. Larger changes (a new screen, a change to navigation or data-mode behaviour)
  get a Linear issue first.
* Each screen is tracked as a Linear issue under YAN-1732, linked to its section of the spec.
* The Integrations screen is designed but is after v1, following the v1 scope. It is built only
  when planned.

## Consequences

* Screens look and behave the same, and builders and reviewers have one place to check against.
* The UI rules from the ADRs are written down per screen, so they are easier to test.
* DESIGN.md has to be kept current. A change in the code that isn't reflected there makes the
  spec wrong for the next person.
* The existing shell (`engineers/base.html`) moves to the design's layout and tokens (YAN-1741).
* Fonts and htmx are served by the controller, so it makes no third-party requests.

## Alternatives considered

* **Design each screen when it is built:** faster per screen, but screens drift and the ADR rules
  get applied unevenly.
* **Port the prototype's code:** it is written in a canvas format with client-side state, which
  ADR-0006 rules out for the UI.
* **Keep the design only in the prototype:** the prototype is private and outside the repo, so
  people and agents working from a checkout couldn't rely on it.

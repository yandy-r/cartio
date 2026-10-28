# Engineer UI design

The engineer UI is built from this design (ADR-0007).

| Path | What it is |
| -- | -- |
| [DESIGN.md](DESIGN.md) | The spec: principles, visual system, app shell, data-mode behaviour and every screen, each linked to its Linear issue. Start here. |
| [prototype/](prototype/) | Source of the interactive B1 prototype, one `.dc.html` file per screen or component, plus `canvas.json` for the board layout. |

## Viewing the prototype

Open the live prototype at <https://claude.ai/artifact/AMnPPqfKSSAQD7uzFJjE25>. It is private,
so ask Yandy to share it. Every screen in it is clickable. The customer switcher at the top of
each screen moves between the five demo customers, which cover Hosted, On site with live view,
inside access only, a completed engagement and an offline collector.

The files in `prototype/` need the design canvas's runtime (`support.js`, not in the repo) to
render, so opening them straight from disk shows unstyled markup. Read them for layout, wording,
states and demo data. The markup between `<x-dc>` tags is the screen; the `Component` class in
the script block holds its state and data.

## Using it

* Build from DESIGN.md. Use the prototype to see what the spec describes.
* Don't copy the prototype's JavaScript into the controller. The UI is Django templates with
  htmx (ADR-0006), and DESIGN.md says how each prototype idea maps onto that.
* All data in the prototype is made up: documentation and private IP ranges, invented names, and
  secrets in their masked form. Keep it that way in any fixture made from it.

## Changing the design

* A PR that builds a screen differently from the design updates DESIGN.md in the same PR and says
  why.
* A new screen, or a change to navigation or data-mode behaviour, gets a Linear issue first.
* Changes to the prototype itself are made in the live canvas, then exported here again so the
  two stay in step. Update the version line at the top of DESIGN.md when you do.

# Cartio engineer UI design (B1)

This is the spec for the engineer UI. Build every screen from it. The interactive prototype in
[prototype/](prototype/) shows the same design, so you can see layout, states and interactions;
this file says what to build. ADR-0007 makes this design the reference for the UI.

Where they disagree, the order is: ADRs, then this file, then the prototype. If a screen needs
to differ from the design, change this file in the same pull request and say why in the PR.

* **Live prototype:** <https://claude.ai/artifact/AMnPPqfKSSAQD7uzFJjE25> (private; ask Yandy for
  access).
* **Version:** B1 draft, 2026-10-28.
* **Tracking:** the Linear issues in the [table of screens](#screens) below. The parent issue is
  YAN-1732.

## How to build from it

ADR-0006 says the engineer UI is server-rendered Django templates with htmx, with no separate
front-end build. The prototype is written in a canvas format with its own JavaScript, so treat
it as a picture of the result, not as code to port:

| In the prototype | In the controller |
| -- | -- |
| One `.dc.html` file per screen | A Django view and template per screen, extending `engineers/base.html` |
| State in the component, shared between screens through `localStorage` and an 800 ms poll | Server state. The current customer and engagement live in the URL (`/customers/<id>/engagements/<id>/...`), so links and the back button work |
| Tabs, filters, modals and side panels that re-render on click | htmx requests that swap a partial (`hx-get` with `hx-target`), with a full-page fallback where it is cheap |
| Hover cards built from data already on the page | The same, from data the page already renders. No extra request on hover |
| Demo data for five customers, written into each file | Real data through the per-customer data layer. The demo customers become a sanitized fixture (see [Demo data](#demo-data)) |
| Buttons that pretend to save, export or start jobs | Real endpoints, each audit-logged where the README says it must be |
| Google Fonts link | Self-hosted font files under the controller's static files. The controller makes no third-party requests |

Keep JavaScript small: htmx plus a few lines where a browser API is needed (the ⌘K shortcut,
copy to clipboard, the topology canvas). If a screen seems to need a front-end framework, raise
it as an ADR rather than adding one.

## Principles

These come from the ADRs and from what the screens are for. Every screen keeps to them.

1. **Read-only, and it says so** (ADR-0004). No control in the UI changes a customer device.
   Recommendations are text and suggested config snippets. The sidebar footer says: "Cartio
   never changes customer devices. Recommendations are text and suggested snippets only."
2. **One customer at a time.** Every screen except All customers and global search shows one
   customer and one engagement, named in the switcher at the top. Nothing on a page mixes data
   from two customers.
3. **Data mode is always visible** (ADR-0002, ADR-0005). A screen for an On site customer says
   where the data is and how the engineer is seeing it: live view (relayed, never stored on the
   controller) or inside access only (not shown on the controller at all).
4. **Secrets never appear.** Configs and command output show `<masked:1:digest>` values, the
   same form the collector produces (ADR-0003). The UI has no "reveal" control.
5. **Nothing stale.** A side panel or detail view shows only what is selected and visible. When
   nothing is selected, it shows a summary of the current view instead of the last thing picked.
6. **Every action leaves a trail.** Edits to findings, starting jobs, saving command output and
   connecting integrations each write an audit entry, and the toast says so.
7. **Use the whole screen.** On wide screens extra space goes to the main content (map, table)
   or to a summary column, not to empty margins. On narrow screens columns stack.

## Visual system

| Token | Value | Use |
| -- | -- | -- |
| Sidebar | `#0E1A24` | Navigation background |
| Sidebar hover / active | `#17293A` / `#1F3A50` | Nav items |
| Sidebar text | `#C9D4DE`, muted `#8FA3B5`, brand mark `#5EC4D6` | |
| Page background | `#F3F5F7` | |
| Surface | `#FFFFFF`, border `#E4E8EC`, control border `#D0D5DD` | Cards, tables, inputs |
| Text | `#101828`, secondary `#475467`, muted `#667085` | |
| Accent | `#0E7490`, hover `#155E75` | Primary buttons, links, focus |
| Critical | `#B42318` | Severity |
| High | `#E8590C` | Severity |
| Medium | `#F5B53F` | Severity |
| Low | `#98A2B3` | Severity |
| Success | `#15803D` | Completed, online |

* **Type:** IBM Plex Sans (400, 500, 600, 700) for the UI, IBM Plex Mono (400, 500) for
  hostnames, IPs, serials, commands, configs and output.
* **Sizes:** body 14px, table 13px, page title 24px semibold, section title 16px semibold, labels
  11–12px uppercase with letter spacing in the sidebar.
* **Controls:** at least 44px tall for buttons and 40px for tabs. Radius 8px for controls and
  10–12px for cards. Pills (`border-radius: 999px`) for chips and filters.
* **Severity** always comes as a colour and a word ("Critical"), never colour alone.
* **Device icons** by role: router, firewall, core switch, access switch, wireless controller,
  access point, server, unknown. The topology map, the report map and the hover cards use the
  same set. They are inline SVG in the prototype (`Topology.dc.html`, the `ICON` map).

## App shell

Every screen has the same frame (`Main.dc.html` shows it best):

* **Sidebar, left, fixed width.** In order:
  * The Cartio mark and "IT Cloud Network".
  * The **global search button** ("Search…  ⌘K").
  * **Workspace:** Customers, Site map, Inventory, Findings, Compare, Report.
  * **Operations:** Collectors & jobs, Integrations, Audit log (the audit tab of Collectors).
  * The read-only note.
  * The signed-in engineer, with "MFA on".
* **Header** on each screen: a small context line, the page title, and the screen's main
  actions on the right.
* **Customer and engagement switcher** (`Switcher.dc.html`) in the sidebar, under the search
  button, on every per-customer screen (where every prototype screen puts it). It lists the
  current customer's engagements with status (Active, Completed), then customers with their data
  mode. Switching keeps the engineer on the same screen.
* The existing shell in `engineers/base.html` (top bar, blue accent) moves to this layout and
  these tokens.

## Context and data modes

The current customer and engagement decide what every screen shows. In the controller they
come from the URL. Global search, the switcher and links from All customers all go to a URL with
both in it.

What a screen shows depends on the customer's data mode:

| Data mode | What the engineer sees | Where saves go |
| -- | -- | -- |
| Hosted | Everything, from the controller database | Controller database ("Cartio DB") |
| On site, live view allowed | Everything, with a banner: "Live view · relayed from the on-site collector, not stored on the controller" | The on-site store, through the collector. Nothing is written on the controller |
| On site, inside access only | The **inside access only** notice (`OnsiteLocked.dc.html`) on every data screen, with how to reach the on-site UI. Customer and collector health still show | Not available from the controller |

* Ostrander Manufacturing is the inside-access-only example in the prototype. Bluepeak Credit
  Union is the live-view example.
* A completed engagement is read-only: a banner says so, and edit controls are hidden. Checks
  can still re-run on the stored model.
* When a collector is offline, screens say so and show the last collected data with its time.

## Screens

| # | Screen | Prototype file | Linear | Milestone |
| -- | -- | -- | -- | -- |
| 1 | [All customers](#1-all-customers) | `Main.dc.html` | YAN-1733 | M1 |
| 2 | [Onboard a customer](#2-onboard-a-customer) | `NewCustomer.dc.html` | YAN-1734 | M1 |
| 3 | [Engagement setup](#3-engagement-setup) | `Engagement.dc.html` | YAN-1735 | M2 |
| 4 | [Collectors, jobs and audit](#4-collectors-jobs-and-audit) | `Collectors.dc.html` | YAN-1736 | M1 |
| 5 | [Site map](#5-site-map) | `Topology.dc.html` | YAN-1663 | M2 |
| 6 | [Inventory](#6-inventory) | `Inventory.dc.html` | YAN-1737 | M2 |
| 7 | [Findings](#7-findings) | `Findings.dc.html` | YAN-1738 | M3 |
| 8 | [Compare](#8-compare) | `Compare.dc.html` | YAN-1667 | M3 |
| 9 | [Report and export](#9-report-and-export) | `Report.dc.html` | YAN-1666 | M3 |
| 10 | [Connect to device](#10-connect-to-device) | `ConnectModal.dc.html` | YAN-1739 | M2 |
| 11 | [Global search](#11-global-search) | `GlobalSearch.dc.html` | YAN-1740 | M2 |
| 12 | [Customer and engagement switcher](#12-customer-and-engagement-switcher) | `Switcher.dc.html` | YAN-1741 | M1 |
| 13 | [Inside access only notice](#13-inside-access-only-notice) | `OnsiteLocked.dc.html` | YAN-1671 | M4 |
| 14 | [Integrations](#14-integrations) | `Integrations.dc.html` | YAN-1742 | After v1 |

### 1. All customers

The landing page after sign-in. An engineer connects once and sees every customer.

* **Header:** "Connected once · 5 customers · 8 collectors", title "All customers", a search box
  for customers, sites and collectors, and the buttons **New engagement** and **New customer**.
* **Customer list:** one row per customer, showing:
  * name and tags (HIPAA, PCI, live view on, inside access only);
  * data mode;
  * current engagement and its status;
  * sites and devices;
  * collector health as chips (a hover card gives host, version, last seen, certificate and
    queue);
  * open findings by severity.
  * A **New engagement** action on each row.
  * Clicking a row opens that customer on the Site map.
* **Side column:** **Needs attention** (an offline collector, an expiring certificate,
  critical findings) and **Recent activity** from the audit log.
* **Empty state:** no customers yet, with **New customer** as the one action.

Until the screens and data they lean on land, the controller's version (YAN-1733) differs:

* A row opens the customer's Overview, with the engagement in the URL, until the Site map
  (YAN-1663) exists.
* **New customer** opens the onboarding wizard (YAN-1734). Engineers who can't add customers,
  sites, engagements and collectors don't see it. **New engagement** opens the admin's add form
  until engagement setup (YAN-1735) exists, and only for engineers who may add engagements.
* Open findings say "No findings yet" until findings (YAN-1738) exist, and the summary has no
  critical findings tile. Tags show compliance (HIPAA, PCI, SOC 2), live view on and inside
  access only.
* The collector hover card says "Not tracked yet" for the certificate and queue until client
  certificates (YAN-1650) and the job channel (YAN-1651) exist. Needs attention lists offline,
  never checked in and not yet enrolled collectors.
* Tabs (All, Hosted, On site, Archived) replace the old "show archived" link. The prototype's
  **Export list** is left out: the issue doesn't ask for it.

### 2. Onboard a customer

A six-step wizard that can be repeated. When it finishes, "Onboard another customer" starts a
fresh one; it never reopens the last customer's final step.

1. **Who is the customer:** name, short name, industry or compliance tags.
2. **Where their network data lives:** Hosted, or On site. On site asks whether live view is
   allowed. The switch starts off and must be chosen (ADR-0005). The choice can't be changed
   here later; changing it is an audited admin action (YAN-1712).
3. **Sites and where discovery starts:** one or more sites with a location and seed hints (IPs,
   networks no wider than /16 or /48, or host names, as the API accepts).
4. **First engagement:** name, type, dates.
5. **Collectors:** one per site by default. Each gets a one-time enrollment token and a copyable
   `cartio-collector enroll` command. The token is shown once, and the screen says so.
6. **Review and create:** a summary of everything. Then a done screen with next steps: enroll
   collectors, set credentials, start discovery.

The step list on the left shows done, current and next. Going back keeps what was entered.

The controller's version (YAN-1734) keeps the wizard's state in the engineer's session and
creates nothing until **Create customer**, which writes everything and its audit events in one
transaction. It differs from the prototype:

* Step 1 asks for the name, compliance (HIPAA, PCI, SOC 2) and notes, as the prototype does.
  There is no short name or industry field.
* Step 2 doesn't ask for the on-site store (database or file share): that comes with the on-site
  store itself (YAN-1670). The live view question shows once On site is picked, and is checked
  on the server either way.
* Seed hints are typed into one box per site, separated by spaces, commas or new lines, and
  checked by the API's own validator on **Continue**, rather than added one chip at a time.
* Step 4 has name, status (Planned or Active), dates and what the assessment covers. Engagements
  have no type yet.
* Collectors have a name but no "Runs on" choice, and their tokens don't expire yet. The tokens
  are only in the done page's response; reloading it starts a fresh wizard.
* The done page's checklist is text. Its PDF, DOCX and email exports, "Set up scope and
  credentials" (YAN-1735) and "Import files instead" (YAN-1662) come later.

### 3. Engagement setup

Opened from **New engagement** or an engagement's settings. Four tabs:

* **Details:** name, type, dates, the engineer. A side column shows previous engagements for
  this customer and a **Ready to start?** checklist (a collector online, scope set, credentials
  tested).
* **Scope** (per site): seeds, allowed ranges and excluded ranges, each with a note. Entries are
  validated as they are typed. **Suggested by integrations** lists seeds from connected
  integrations, with Accept, Dismiss and Accept all (after v1; hidden until integrations exist).
* **Credentials:** SSH, SNMPv3 and API credentials for this engagement, each with **Test**
  through the collector. Values are write-only. For On site customers a note says credentials
  stay on site (YAN-1670).
* **Launch:** review, then **Start discovery**, which queues a job and opens Collectors & jobs.

### 4. Collectors, jobs and audit

Three tabs for the current customer:

* **Collectors:** state (online, offline, not enrolled, revoked), host, site, version, last
  check-in, certificate expiry and queue length. **Add a collector** opens a short wizard (name,
  site, token, enroll command). **Revoke** asks for confirmation and is audit-logged.
* **Jobs:** discovery, import, health and config capture jobs, with state and progress.
  **Start a discovery** links to Engagement setup's Launch tab, and **Run health check** starts
  a health job (YAN-1725).
* **Audit:** the customer's audit log with time, kind, who and what. It can be filtered by kind.
  The sidebar's **Audit log** link opens this tab.

As built (YAN-1736):

* Each tab is its own URL (`collectors`, `jobs`, `audit`), so the switcher and links keep the
  tab. Jobs and Audit show only with the permissions their APIs ask for.
* A collector has an optional site. Revoking clears its secret and any unused token, so it can't
  check in, start a run or receive credentials again; a collector that never enrolled shows
  **Cancel enrollment** instead. The confirmation is on the collector's card.
* The wizard asks for name and site only. The prototype's "Runs on" choice and the 24-hour
  check-in sparkline are left out until the controller records them. Certificate expiry and the
  offline queue read "Not tracked yet" until client certificates (YAN-1650) and the job channel
  (YAN-1651) exist. Enrollment tokens don't expire yet.
* **Start a discovery** and **Run health check** are shown disabled until Engagement setup
  (YAN-1735) and health jobs (YAN-1725) land.
* The audit filter kinds are Jobs, Collectors, Credentials and Admin. Sign-ins aren't tied to a
  customer (`LoginEvent`), so there is no Logins filter. **Export CSV** downloads the whole log
  and is itself logged.

### 5. Site map

The per-site L2/L3 map (YAN-1663), the main picture of a customer's network.

* **Toolbar:** site picker, an **L2 / L3** toggle, filters (role, vendor, has findings), zoom,
  and **Fit**. The map fits the space available by default and refits when the window resizes.
* **Map:** devices as role icons with hostname and model, and links between them. L2 links come
  from LLDP/CDP, labelled with interfaces on hover. L3 links come from routing neighbours,
  labelled with protocol and subnet. Devices with findings carry a severity dot. Unconfirmed
  devices (seen as a neighbour but not polled) are drawn dashed.
* **Hover card on a device:** hostname, IP, model, serial, OS, role, site, findings and last
  seen.
* **Click a device** to pin it. The side panel then shows details, its links, findings, and
  **Connect to…**. Clicking the background, pressing **Esc**, the clear button or changing site
  unpins it.
* **Side panel with nothing pinned:** a **site summary**: device counts by role, findings by
  severity, and devices that need attention. Never the last device that was pinned.
* **Export:** PNG and SVG of the current view, and the device list as CSV.

### 6. Inventory

Every device in the engagement.

* **Table columns:** hostname, IP, vendor and model, serial, OS and its lifecycle state, site,
  source, findings and last seen. Plus **Connect** per row.
* **Lifecycle state:** supported, EoS soon, EoS or EoL, unknown, pending.
* **Source:** discovery, import, SNMP only, or an integration ("From Site24x7").
* **Badges:** an **Also in** badge marks devices that integrations also know about.
* **Search and filters:** search covers hostname, IP, serial and model. Filters are pills for
  vendor and OS state. Columns sort.
* **Wide screens:** a summary column with breakdowns by vendor, OS state and site. Clicking a
  breakdown applies it as a filter.
* **Hover card on a row:** the full device record, its interfaces and neighbours count, and
  where else it was seen.
* **Selection and actions:** selecting rows enables **Export** (CSV, XLSX). Only visible rows
  count as selected; if filters hide selected rows, the footer says so. **Import** takes show
  outputs, configs and CSV device lists (YAN-1662).
* **Pending devices:** devices suggested by an integration and not yet discovered show as
  pending until discovery confirms them.
* **Arriving from global search:** the search box is filled with the hostname.

### 7. Findings

The results of the five v1 checks (YAN-1665), which the engineer can correct before the report.

* **Left:** the five checks (EoL/EoS versions, firmware drift, insecure management, NTP and
  syslog, SNMPv3 in use) with pass and fail counts. Clicking a check filters the list.
* **Middle:** findings, each with severity, title, affected device count and an **In report**
  state.
* **Detail:**
  * what was found and why it matters;
  * the affected devices as chips (each with **Connect**);
  * evidence lines from the collected data;
  * the recommendation and a suggested config snippet;
  * engineer notes.
  * Evidence from integrations is shown separately and labelled with its source (after v1).
* **Engineer edits:** **Add finding** (manual findings are marked "Added by engineer"),
  **Edit**, **Include in report** on or off, and **Dismiss** for check findings or **Delete**
  for manual ones. Dismiss and Delete need a reason. A **Dismissed** list can restore them.
  Every edit is audit-logged, and the report uses the edited set.
* **Detail rule:** the detail shows a finding from the visible list only. With no findings it
  shows an empty state, not a stale finding.
* **Arriving from global search:** the finding is selected and the check filter is cleared.
  The finding's id goes in the URL (`?finding=<id>`); a check finding's id is its check id.
* **As built (YAN-1738):** each check that fails on at least one device in the latest run is
  one finding, with the failing devices as its affected devices. The prototype splits one
  check into several findings (Telnet and SNMP write under insecure management, for example);
  the v1 checks report per device, not per cause, so they can't yet. An engineer who wants them
  apart edits the check finding and adds a manual one. Edits to a check finding are kept on top
  of the check's result, so re-running the checks keeps them. Findings for On site customers
  aren't kept on the controller, so the screen shows no edit controls for them until saving
  through the collector exists. Findings CSV export and evidence from integrations aren't built
  yet.

### 8. Compare

Two engagements of the same customer side by side (YAN-1667).

* **Engagements:** pickers for the earlier and the later engagement.
* **Summary:** devices added, removed and changed; findings new, fixed and still open; and OS
  changes.
* **Config diff:** per device, side by side or unified. Masked secrets compare by digest, so a
  changed secret shows as changed without its value.
* **One engagement only:** a customer with a single engagement gets an empty state explaining
  that Compare needs two.

### 9. Report and export

The customer-ready assessment report (YAN-1666), previewed as pages:

1. **Cover**, with customer, engagement, dates and engineer.
2. **Summary:** findings by severity and the top risks in plain words.
3. **Scope and method:** sites, collectors, what was read and how, and the read-only statement.
4. **Topology:** a per-site L2 map with the same icons and links as the Site map, not plain
   boxes.
5. **Findings:** the engineer-edited set, with recommendations and snippets, and evidence
   where included.
6. **Changes since the last assessment**, from Compare. Hidden when there is no earlier
   engagement.
7. **Capacity and availability:** port use, PoE budget, uptime. Items noted from integrations
   go here after v1.

Actions:

* **Export PDF** and **Export DOCX**, plus **CSV** of findings.
* For On site customers the report is built and saved on site, and the screen shows saved, or
  not saved yet with the reason (ADR-0005).

### 10. Connect to device

A modal opened from **Connect to…** anywhere a device appears: Site map, Inventory, Findings
chips, and search results. It does not appear in the report. It is how an engineer runs manual,
read-only discovery against one device through its collector.

* **Size:** large and responsive. It is nearly full screen on wide displays and a full-screen
  sheet on narrow ones. **Maximize** and **Output only** give the output more room.
* **Left:**
  * the device (platform detected from model and OS);
  * the connection methods it supports (SSH, REST API, SNMP), each with the collector that will
    reach it and the credential it will use;
  * **command templates** for the platform: Cisco IOS and IOS-XE show commands, FortiOS CLI and
    REST monitor calls, and SNMP walks.
* **Custom commands:** checked against the read-only allow-list (YAN-1656) before they run.
  Write commands, chained commands, redirects and non-GET API calls are rejected with the reason
  shown.
* **Output:**
  * **Full output**, never cut short, in Plex Mono;
  * line numbers, filter, collapse per command, and copy;
  * secrets masked.
* **Save:** **Save to database** stores the output with the device and engagement ("Cartio DB"
  for Hosted, "on-site store" for On site). Saving is audit-logged. **Download** gives a text
  file.

### 11. Global search

One search for everything an engineer can open, from the sidebar button or **⌘K** / **Ctrl K**.
**Esc** closes it, the arrow keys move through results, and **Enter** opens the selected one.

* **Searches:**
  * devices by hostname, IP, serial, model and site;
  * findings by title, check and device;
  * customers, engagements, collectors and integrations;
  * the screens themselves.
* **Matching:** words match in any order and in part ("fortigate 60f", "192.0.2.1").
* **Results:** grouped by type, with the customer named on each.
* **Scope toggle:** **All customers** or **Only current customer**.
* **Opening a result:** it switches customer and engagement first, then goes to the screen. A
  device opens Inventory filtered to it, and a finding opens Findings with it selected.
* **Isolation:** results come only from customers the engineer may see, and each result is
  resolved within its own customer's scope. Customers that are inside access only have no
  device or finding data on the controller to search; search says so. Live view customers are
  searched through the relay and nothing is indexed on the controller.
* **Until those features land:** findings and integrations join the search with their screens
  (YAN-1738, YAN-1742), and live view search waits for the relay through the collector. Until
  then search leaves those out and its note says why for each On site customer. Devices open
  Inventory filtered by host name, and engagements open the customer's overview, since Report
  isn't built yet.

### 12. Customer and engagement switcher

A compact control in the sidebar: the current customer and engagement. Its menu lists the
current customer's engagements with status, and then customers (name, data mode, and for On site
whether live view is on). Picking an engagement reloads the current screen for it; picking
another customer reloads the screen for that customer's current engagement. Typing in the menu
filters customers with an htmx request. Only the current customer's engagements are listed, so
the menu never mixes two customers' records.

### 13. Inside access only notice

Shown in place of a data screen for an On site customer whose live view is off. It says:

* which screen and customer it is;
* that the data never leaves the customer network;
* how to reach the collector's local UI from inside.

It still shows customer and collector health, which the controller keeps (ADR-0005).

### 14. Integrations

**After v1.** The v1 scope puts pulls from customers' own tools under Later. The screen is
designed now so the data model leaves room for it. Build it only when it is planned.

* **Catalogue:**
  * SolarWinds NPM, ManageEngine OpManager, Site24x7, ExtraHop RevealX, NetBrain, NetBox, PRTG,
    Auvik, LibreNMS and ServiceNow CMDB;
  * each with a category, state (Connected, Available, Error) and the kinds of data it gives;
  * connected integrations first.
* **Modal tabs:**
  * **What we take:** read-only data only;
  * **Field mapping** to Cartio's device fields;
  * **What it found**, with actions;
  * **Connect / Settings:** a read-only API account, and where the token is stored.
* **What it found** has these actions:
  * add seeds to an engagement's scope;
  * add pending devices to Inventory;
  * attach evidence to a finding or create one;
  * add a note to the report's capacity section.
  * Each action is audit-logged and can be undone until accepted.
* **Demo:** Site24x7 is connected for Northwind and Bluepeak.

## Hover cards, toasts and export

* **Hover cards:** open on hover and on keyboard focus, and close on blur or **Esc**. They never
  hide the thing they describe and never need a click to read.
* **Toasts:** confirm actions in one line ("Finding updated · logged in the audit log") and
  disappear by themselves.
* **Export:** available from Site map (PNG, SVG, CSV), Inventory (CSV, XLSX), Findings (CSV),
  Report (PDF, DOCX) and Connect output (text). Exports carry the customer, engagement and time,
  and are audit-logged.

## Accessibility

* Every control works by keyboard, with a visible focus ring in the accent colour.
* Modals trap focus and return it when they close.
* Colour is never the only signal: severity and states have words too.
* Text contrast meets WCAG AA on both the sidebar and the page.
* Pressed and selected states use `aria-pressed` or `aria-current`.

## Demo data

The prototype uses five made-up customers to show every mode:

| Customer | Mode | Shows |
| -- | -- | -- |
| Northwind Clinics | Hosted, HIPAA | The full flow; three sites; Site24x7 connected |
| Bluepeak Credit Union | On site, live view on | Live view banners and saving to the on-site store |
| Larkfield School District | Hosted | A completed engagement and an offline collector |
| Ostrander Manufacturing | On site, inside access only | The inside access only notice |
| ITCN Lab | Hosted | The internal lab |

All addresses are documentation or private ranges (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24, 10.0.0.0/8), and all secrets are masked. If a sanitized demo fixture is added
for local development, it goes under `tests/fixtures/sanitized/` and keeps to the same rules.

## Not in the design on purpose

* No control that changes a device: no config push, no reload, no "fix it" button (ADR-0004).
* No way to see a secret's value.
* No customer logins or customer-facing portal (v1 scope).
* No real-time monitoring or alerting views (v1 scope).

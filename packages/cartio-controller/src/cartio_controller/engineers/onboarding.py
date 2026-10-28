"""Onboard a customer (DESIGN.md section 2): a six-step wizard kept in the engineer's session.

Nothing is created until the last step. Each step posts the whole form, so going back, or to an
earlier step from the step list, keeps what was entered. Creating the customer writes the
customer, its sites, engagement and collectors and their audit events in one transaction, and
clears the wizard: the next visit starts a fresh one. Enrollment tokens are only in the response
that shows them; the controller keeps their hashes.
"""

import re
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import AuditEvent, Collector, Customer, Engagement, Site
from cartio_controller.collectors.validators import MAX_SEED_HINTS, normalize_seed_hint

SESSION_KEY = "cartio_onboarding"

STEPS = ["Customer", "Data residency", "Sites", "Engagement", "Collectors", "Review"]
LAST = len(STEPS)

MAX_SITES = 50
MAX_NAME = 200
MAX_LOCATION = 500
MAX_NOTES = 5000
MAX_ASSESSMENT = 5000

DEFAULT_ENGAGEMENT = "Initial network assessment"
DEFAULT_ASSESSMENT = (
    "Baseline inventory and topology for all sites, the five v1 checks, and a report with "
    "recommended fixes and enhancements."
)

PERMISSIONS = (
    "collectors.add_customer",
    "collectors.add_site",
    "collectors.add_engagement",
    "collectors.add_collector",
)

_SEED_SEPARATORS = re.compile(r"[\s,]+")


def can_onboard(user) -> bool:
    return user.has_perms(PERMISSIONS)


def _site(name="") -> dict:
    return {"name": name, "location": "", "seeds": "", "collector": True, "collector_name": ""}


def fresh() -> dict:
    return {
        "step": 1,
        "reached": 1,
        "name": "",
        "compliance": [],
        "notes": "",
        "data_mode": "",
        "live_view": "",
        "sites": [_site()],
        "skip_engagement": False,
        "engagement": {
            "name": DEFAULT_ENGAGEMENT,
            "status": Engagement.Status.PLANNED,
            "start_date": "",
            "end_date": "",
            "assessment": DEFAULT_ASSESSMENT,
        },
    }


def _text(post, key, limit) -> str:
    return post.get(key, "").strip()[:limit]


def save_input(state: dict, post) -> None:
    """Keep what the current step's form holds, valid or not."""
    step = state["step"]
    if step == 1:
        state["name"] = _text(post, "name", MAX_NAME + 1)
        known = {value for value, _ in Customer.Compliance.choices}
        state["compliance"] = [v for v in dict.fromkeys(post.getlist("compliance")) if v in known]
        state["notes"] = _text(post, "notes", MAX_NOTES + 1)
    elif step == 2:
        mode = post.get("data_mode", "")
        state["data_mode"] = mode if mode in Customer.DataMode.values else ""
        live = post.get("live_view", "")
        state["live_view"] = live if live in ("yes", "no") else ""
    elif step == 3:
        for index, site in enumerate(state["sites"]):
            site["name"] = _text(post, f"site-{index}-name", MAX_NAME + 1)
            site["location"] = _text(post, f"site-{index}-location", MAX_LOCATION + 1)
            site["seeds"] = post.get(f"site-{index}-seeds", "").strip()[:20000]
    elif step == 4:
        state["skip_engagement"] = post.get("skip_engagement") == "on"
        engagement = state["engagement"]
        engagement["name"] = _text(post, "engagement_name", MAX_NAME + 1)
        status = post.get("engagement_status", "")
        engagement["status"] = status if status in ("planned", "active") else "planned"
        engagement["start_date"] = _text(post, "start_date", 10)
        engagement["end_date"] = _text(post, "end_date", 10)
        engagement["assessment"] = _text(post, "assessment", MAX_ASSESSMENT + 1)
    elif step == 5:
        for index, site in enumerate(state["sites"]):
            site["collector"] = post.get(f"collector-{index}") == "on"
            site["collector_name"] = _text(post, f"collector-{index}-name", MAX_NAME + 1)


def add_site(state: dict) -> None:
    if len(state["sites"]) < MAX_SITES:
        state["sites"].append(_site())


def remove_site(state: dict, index: int) -> None:
    if len(state["sites"]) > 1 and 0 <= index < len(state["sites"]):
        del state["sites"][index]


def seed_hints(raw: str) -> tuple[list[str], list[str]]:
    """The site's seed hints as the API stores them, and an error for each one that isn't valid."""
    hints, errors = [], []
    for value in filter(None, _SEED_SEPARATORS.split(raw)):
        try:
            hints.append(normalize_seed_hint(value))
        except ValidationError as exc:
            errors.extend(exc.messages)
    hints = list(dict.fromkeys(hints))
    if len(hints) > MAX_SEED_HINTS:
        errors.append(f"At most {MAX_SEED_HINTS} seed hints per site.")
    return hints, errors


def _prefix(name: str) -> str:
    initials = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", name))
    return initials.lower()[:3] or "cu"


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return words[0] if words else "site"


def default_collector_name(state: dict, index: int) -> str:
    site = state["sites"][index]["name"] or f"site{index + 1}"
    return f"{_prefix(state['name'])}-{_slug(site)}-vm"


def _parse_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return False


def errors_for(state: dict, step: int) -> dict:
    """What stops the step from being complete, by field. Empty when the step is complete."""
    errors = {}
    if step == 1:
        name = state["name"]
        if not name:
            errors["name"] = "Enter the customer's name."
        elif len(name) > MAX_NAME:
            errors["name"] = f"Use at most {MAX_NAME} characters."
        # Names are unique ignoring case, archived customers included.
        elif Customer.objects.filter(name__iexact=name).exists():
            errors["name"] = (
                "A customer with this name already exists (names are compared without case). "
                "Archived customers count too."
            )
        if len(state["notes"]) > MAX_NOTES:
            errors["notes"] = f"Use at most {MAX_NOTES} characters."
    elif step == 2:
        if not state["data_mode"]:
            errors["data_mode"] = "Choose where their network data lives."
        elif state["data_mode"] == Customer.DataMode.ON_SITE and not state["live_view"]:
            errors["live_view"] = "Decide on live view. There is no default."
    elif step == 3:
        seen = set()
        for index, site in enumerate(state["sites"]):
            name = site["name"]
            if not name:
                errors[f"site-{index}-name"] = "Every site needs a name."
            elif len(name) > MAX_NAME:
                errors[f"site-{index}-name"] = f"Use at most {MAX_NAME} characters."
            elif name in seen:
                errors[f"site-{index}-name"] = "Two sites can't share a name."
            seen.add(name)
            if len(site["location"]) > MAX_LOCATION:
                errors[f"site-{index}-location"] = f"Use at most {MAX_LOCATION} characters."
            _, seed_errors = seed_hints(site["seeds"])
            if seed_errors:
                errors[f"site-{index}-seeds"] = " ".join(seed_errors)
    elif step == 4 and not state["skip_engagement"]:
        engagement = state["engagement"]
        if not engagement["name"]:
            errors["engagement_name"] = "Name the engagement, or skip it for now."
        elif len(engagement["name"]) > MAX_NAME:
            errors["engagement_name"] = f"Use at most {MAX_NAME} characters."
        start = _parse_date(engagement["start_date"])
        end = _parse_date(engagement["end_date"])
        if start is False:
            errors["start_date"] = "Enter a date like 2026-11-03."
        if end is False:
            errors["end_date"] = "Enter a date like 2026-11-21."
        elif start and end and end < start:
            errors["end_date"] = "Must be on or after the start date."
        if len(engagement["assessment"]) > MAX_ASSESSMENT:
            errors["assessment"] = f"Use at most {MAX_ASSESSMENT} characters."
    elif step == 5:
        seen = set()
        for index, site in enumerate(state["sites"]):
            if not site["collector"]:
                continue
            name = site["collector_name"]
            if not name:
                errors[f"collector-{index}-name"] = "Name the collector."
            elif len(name) > MAX_NAME:
                errors[f"collector-{index}-name"] = f"Use at most {MAX_NAME} characters."
            elif name in seen:
                errors[f"collector-{index}-name"] = "Two collectors can't share a name."
            seen.add(name)
    return errors


def first_incomplete(state: dict) -> int | None:
    for step in range(1, LAST):
        if errors_for(state, step):
            return step
    return None


def go_to(state: dict, step: int) -> None:
    step = max(1, min(step, LAST))
    if step == 5:
        # One collector per site by default, named after the customer and site.
        for index, site in enumerate(state["sites"]):
            if not site["collector_name"]:
                site["collector_name"] = default_collector_name(state, index)
    state["step"] = step
    state["reached"] = max(state["reached"], step)


def can_open(state: dict, step: int) -> bool:
    """Whether the step list lets the engineer jump to `step`: reached before, or next in line."""
    if step <= state["reached"]:
        return True
    return all(not errors_for(state, earlier) for earlier in range(1, step))


def summary(state: dict) -> dict:
    """The step list's subtitles and the review rows."""
    sites = state["sites"]
    on_site = state["data_mode"] == Customer.DataMode.ON_SITE
    if state["data_mode"] == Customer.DataMode.HOSTED:
        mode = "Hosted"
    elif on_site:
        mode = "On site" + {"yes": " · live view allowed", "no": " · inside access only"}.get(
            state["live_view"], ""
        )
    else:
        mode = ""
    labels = dict(Customer.Compliance.choices)
    compliance = [labels[value] for value in state["compliance"]]
    seeds = sum(len(seed_hints(site["seeds"])[0]) for site in sites)
    collectors = [site["collector_name"] for site in sites if site["collector"]]
    engagement = state["engagement"]
    if state["skip_engagement"]:
        engagement_text = ""
    else:
        dates = " – ".join(filter(None, [engagement["start_date"], engagement["end_date"]]))
        status = Engagement.Status(engagement["status"]).label
        engagement_text = " · ".join(filter(None, [engagement["name"], status, dates]))
    return {
        "customer": " · ".join(filter(None, [state["name"], ", ".join(compliance)])),
        "mode": mode,
        "sites": f"{len(sites)} site{'s' if len(sites) != 1 else ''} · "
        f"{seeds} seed{'s' if seeds != 1 else ''}",
        "site_list": ", ".join(
            f"{site['name'] or 'Unnamed'} ({len(seed_hints(site['seeds'])[0])} seeds)"
            for site in sites
        ),
        "engagement": engagement_text,
        "collectors": collectors,
        "compliance": compliance,
    }


def create(state: dict, actor: str) -> tuple[Customer, list[dict]]:
    """Create everything the wizard holds, audit-logged. Returns the customer and the tokens.

    Raises IntegrityError when a customer of the same name was created meanwhile.
    """
    on_site = state["data_mode"] == Customer.DataMode.ON_SITE
    with transaction.atomic():
        customer = Customer.objects.create(
            name=state["name"],
            data_mode=state["data_mode"],
            # ADR-0005: chosen explicitly for On site, and meaningless for Hosted.
            live_view_allowed=on_site and state["live_view"] == "yes",
            compliance=state["compliance"],
            notes=state["notes"],
        )

        def log(record, action, **detail):
            audit.record(
                customer,
                action,
                actor=actor,
                object_type=record._meta.model_name,
                object_id=record.pk,
                object_name=record.name,
                via="onboarding",
                **detail,
            )

        log(
            customer,
            AuditEvent.Action.RECORD_CREATED,
            data_mode=customer.data_mode,
            live_view_allowed=customer.live_view_allowed,
        )
        site_records = []
        for site in state["sites"]:
            hints, errors = seed_hints(site["seeds"])
            if errors:
                raise ValidationError(errors)
            record = Site.objects.for_customer(customer).create(
                name=site["name"], location=site["location"], seed_hints=hints
            )
            log(record, AuditEvent.Action.RECORD_CREATED)
            site_records.append(record)
        if not state["skip_engagement"]:
            engagement = state["engagement"]
            record = Engagement.objects.for_customer(customer).create(
                name=engagement["name"],
                status=engagement["status"],
                start_date=_parse_date(engagement["start_date"]),
                end_date=_parse_date(engagement["end_date"]),
                assessment=engagement["assessment"],
            )
            log(record, AuditEvent.Action.RECORD_CREATED)
        tokens = []
        for site, site_record in zip(state["sites"], site_records, strict=True):
            if not site["collector"]:
                continue
            collector = Collector(customer=customer, name=site["collector_name"], site=site_record)
            token = collector.issue_enrollment_token()
            collector.save()
            log(collector, AuditEvent.Action.COLLECTOR_CREATED, site=site["name"])
            tokens.append({"name": collector.name, "site": site["name"], "token": token})
    return customer, tokens

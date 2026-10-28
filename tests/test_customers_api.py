import pytest
from cartio_controller.collectors.models import Customer, Engagement, Site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

CUSTOMERS = "/api/v1/customers"


def _engineer(username="engineer"):
    staff = get_user_model().objects.create_user(username, password="x", is_staff=True)
    staff.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label="collectors",
            content_type__model__in=["customer", "engagement", "site"],
        )
    )
    return staff


@pytest.fixture
def api(client):
    client.force_login(_engineer())
    return client


def _post(client, path, body=None):
    return client.post(path, data=body or {}, content_type="application/json")


def _patch(client, path, body):
    return client.patch(path, data=body, content_type="application/json")


def _customer(client, name="Acme", **fields):
    response = _post(client, CUSTOMERS, {"name": name, **fields})
    assert response.status_code == 201, response.json()
    return response.json()


def _names(response):
    assert response.status_code == 200
    return [row["name"] for row in response.json()["results"]]


# Customers


def test_customer_defaults_to_hosted(api):
    body = _customer(api)
    assert body["data_mode"] == "hosted"
    assert body["archived_at"] is None
    assert Customer.objects.get(pk=body["id"]).data_mode == Customer.DataMode.HOSTED


def test_customer_can_be_created_on_site(api):
    assert _customer(api, data_mode="on_site")["data_mode"] == "on_site"


@pytest.mark.parametrize("mode", ["", None, "cloud"])
def test_customer_rejects_an_empty_or_unknown_data_mode(api, mode):
    response = _post(api, CUSTOMERS, {"name": "Acme", "data_mode": mode})
    assert response.status_code == 400
    assert "data_mode" in response.json()


def test_database_rejects_an_unknown_data_mode():
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.create(name="Acme", data_mode="cloud")


def test_customer_list_update_and_unique_name(api):
    acme = _customer(api, "Acme")
    _customer(api, "Globex")
    assert _names(api.get(CUSTOMERS)) == ["Acme", "Globex"]

    response = _patch(api, f"{CUSTOMERS}/{acme['id']}", {"notes": "Two offices"})
    assert response.status_code == 200
    assert response.json()["notes"] == "Two offices"

    assert _post(api, CUSTOMERS, {"name": "Globex"}).status_code == 400


def test_customer_data_mode_cannot_change_by_update(api):
    acme = _customer(api)
    response = _patch(api, f"{CUSTOMERS}/{acme['id']}", {"data_mode": "on_site"})
    assert response.status_code == 400
    assert Customer.objects.get(pk=acme["id"]).data_mode == "hosted"

    # Sending the current value is fine, so full updates work.
    same = api.put(
        f"{CUSTOMERS}/{acme['id']}",
        data={"name": "Acme Corp", "data_mode": "hosted"},
        content_type="application/json",
    )
    assert same.status_code == 200


def test_customer_archive_and_restore(api):
    acme = _customer(api, "Acme")
    _customer(api, "Globex")
    url = f"{CUSTOMERS}/{acme['id']}"

    archived = _post(api, f"{url}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert _names(api.get(CUSTOMERS)) == ["Globex"]
    assert _names(api.get(CUSTOMERS, {"archived": "true"})) == ["Acme", "Globex"]
    assert api.get(url).status_code == 200

    assert _patch(api, url, {"notes": "x"}).status_code == 400

    restored = _post(api, f"{url}/restore")
    assert restored.json()["archived_at"] is None
    assert _names(api.get(CUSTOMERS)) == ["Acme", "Globex"]


def test_customers_cannot_be_deleted(api):
    acme = _customer(api)
    assert api.delete(f"{CUSTOMERS}/{acme['id']}").status_code == 405
    assert Customer.objects.filter(pk=acme["id"]).exists()


# Engagements and sites


def test_engagement_lifecycle(api):
    acme = _customer(api)
    url = f"{CUSTOMERS}/{acme['id']}/engagements"

    response = _post(
        api,
        url,
        {
            "name": "2026 Q4 assessment",
            "start_date": "2026-11-02",
            "end_date": "2026-11-20",
            "assessment": "Firewall and switching review",
        },
    )
    assert response.status_code == 201, response.json()
    engagement = response.json()
    assert engagement["customer"] == acme["id"]
    assert engagement["status"] == "planned"

    detail = f"{url}/{engagement['id']}"
    assert _patch(api, detail, {"status": "active"}).json()["status"] == "active"
    assert _patch(api, detail, {"status": "paused"}).status_code == 400
    assert _names(api.get(url)) == ["2026 Q4 assessment"]

    assert _post(api, f"{detail}/archive").status_code == 200
    assert _names(api.get(url)) == []
    assert _names(api.get(url, {"archived": "1"})) == ["2026 Q4 assessment"]


def test_engagement_end_date_must_not_precede_start(api):
    acme = _customer(api)
    url = f"{CUSTOMERS}/{acme['id']}/engagements"
    bad = {"name": "E", "start_date": "2026-11-20", "end_date": "2026-11-02"}
    assert _post(api, url, bad).status_code == 400

    ok = _post(api, url, {"name": "E", "start_date": "2026-11-02"}).json()
    response = _patch(api, f"{url}/{ok['id']}", {"end_date": "2026-11-01"})
    assert response.status_code == 400


def test_site_lifecycle_and_seed_hints(api):
    acme = _customer(api)
    url = f"{CUSTOMERS}/{acme['id']}/sites"
    seeds = ["192.0.2.1", "198.51.100.0/24", "2001:db8::1", "core-sw1.example.net"]

    response = _post(api, url, {"name": "HQ", "location": "Main office", "seed_hints": seeds})
    assert response.status_code == 201, response.json()
    site = response.json()
    assert site["seed_hints"] == seeds
    assert site["customer"] == acme["id"]

    response = _patch(api, f"{url}/{site['id']}", {"location": "Second floor"})
    assert response.json()["location"] == "Second floor"


def test_site_seed_hints_are_normalized(api):
    acme = _customer(api)
    hints = ["192.0.2.7/24", "198.51.100.9/32", "Core-SW1.Example.NET.", "192.0.2.0/24"]
    response = _post(api, f"{CUSTOMERS}/{acme['id']}/sites", {"name": "HQ", "seed_hints": hints})
    assert response.status_code == 201, response.json()
    assert response.json()["seed_hints"] == ["192.0.2.0/24", "198.51.100.9", "core-sw1.example.net"]


@pytest.mark.parametrize(
    "hint",
    [
        "",
        "192.0.2.300",
        "-bad.example",
        "a b",
        "x" * 300,
        "evil\n.example.com",
        "fe80::1%$(id)",
        "host;id",
        "0.0.0.0/0",
        "::/0",
        "10.0.0.0/8",
        "224.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",  # noqa: S104
        "localhost",
        "0x7f000001",
        "metadata.0xa9fea9fe",
    ],
)
def test_site_rejects_bad_seed_hints(api, hint):
    acme = _customer(api)
    response = _post(api, f"{CUSTOMERS}/{acme['id']}/sites", {"name": "HQ", "seed_hints": [hint]})
    assert response.status_code == 400


@pytest.mark.parametrize("kind", ["engagements", "sites"])
def test_names_are_unique_per_customer(api, kind):
    acme = _customer(api, "Acme")
    globex = _customer(api, "Globex")
    assert _post(api, f"{CUSTOMERS}/{acme['id']}/{kind}", {"name": "One"}).status_code == 201
    assert _post(api, f"{CUSTOMERS}/{acme['id']}/{kind}", {"name": "One"}).status_code == 400
    assert _post(api, f"{CUSTOMERS}/{globex['id']}/{kind}", {"name": "One"}).status_code == 201


@pytest.mark.parametrize("kind", ["engagements", "sites"])
def test_archived_customer_takes_no_new_records(api, kind):
    acme = _customer(api)
    _post(api, f"{CUSTOMERS}/{acme['id']}/archive")
    assert _post(api, f"{CUSTOMERS}/{acme['id']}/{kind}", {"name": "One"}).status_code == 400


@pytest.mark.parametrize("kind", ["engagements", "sites"])
def test_unknown_customer_is_not_found(api, kind):
    assert api.get(f"{CUSTOMERS}/999999/{kind}").status_code == 404
    assert _post(api, f"{CUSTOMERS}/999999/{kind}", {"name": "One"}).status_code == 404


# Every engagement and site belongs to exactly one customer.


@pytest.mark.parametrize("kind", ["engagements", "sites"])
def test_records_stay_with_their_customer(api, kind):
    acme = _customer(api, "Acme")
    globex = _customer(api, "Globex")
    acme_url = f"{CUSTOMERS}/{acme['id']}/{kind}"
    globex_url = f"{CUSTOMERS}/{globex['id']}/{kind}"

    # A customer in the body is ignored: the URL decides.
    record = _post(api, acme_url, {"name": "One", "customer": globex["id"]}).json()
    assert record["customer"] == acme["id"]

    # Moving it to another customer by update is ignored too.
    moved = _patch(api, f"{acme_url}/{record['id']}", {"customer": globex["id"]})
    assert moved.json()["customer"] == acme["id"]

    # Another customer's path can't list, read, change or archive it.
    assert _names(api.get(globex_url)) == []
    assert api.get(f"{globex_url}/{record['id']}").status_code == 404
    assert _patch(api, f"{globex_url}/{record['id']}", {"name": "Two"}).status_code == 404
    assert _post(api, f"{globex_url}/{record['id']}/archive").status_code == 404
    assert _post(api, f"{globex_url}/{record['id']}/restore").status_code == 404

    model = Engagement if kind == "engagements" else Site
    stored = model.all_customers.get(pk=record["id"])
    assert stored.customer_id == acme["id"]
    assert stored.name == "One"
    assert stored.archived_at is None


def test_engagement_and_site_require_a_customer():
    with pytest.raises(IntegrityError), transaction.atomic():
        Engagement.all_customers.create(name="Orphan")
    with pytest.raises(IntegrityError), transaction.atomic():
        Site.all_customers.create(name="Orphan")


def test_customer_with_records_cannot_be_hard_deleted():
    from django.db.models import ProtectedError

    acme = Customer.objects.create(name="Acme")
    Site.objects.for_customer(acme).create(name="HQ")
    with pytest.raises(ProtectedError):
        acme.delete()


# Access


@pytest.mark.parametrize("path", [CUSTOMERS, f"{CUSTOMERS}/1/engagements", f"{CUSTOMERS}/1/sites"])
def test_api_needs_a_staff_login(client, path):
    Customer.objects.create(pk=1, name="Acme")
    assert client.get(path).status_code == 403
    assert _post(client, path, {"name": "x"}).status_code == 403

    user = get_user_model().objects.create_user("someone", password="x")
    client.force_login(user)
    assert client.get(path).status_code == 403


def test_api_needs_csrf_for_writes():
    from django.test import Client

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(_engineer())
    assert _post(csrf_client, CUSTOMERS, {"name": "Acme"}).status_code == 403


def test_create_collector_refuses_an_archived_customer():
    Customer.objects.create(name="Acme", archived_at="2026-10-01T00:00:00Z")
    with pytest.raises(CommandError):
        call_command("create_collector", customer="Acme", name="vm01")


def test_staff_without_permissions_cannot_write(client):
    client.force_login(get_user_model().objects.create_user("viewer", is_staff=True))
    assert _post(client, CUSTOMERS, {"name": "Acme"}).status_code == 403
    assert not Customer.objects.exists()


def test_customer_names_are_unique_ignoring_case(api):
    _customer(api, "Acme")
    assert _post(api, CUSTOMERS, {"name": "ACME"}).status_code == 400
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.create(name="acme")


@pytest.mark.parametrize("kind", ["engagements", "sites"])
def test_records_of_an_archived_customer_are_frozen(api, kind):
    acme = _customer(api)
    url = f"{CUSTOMERS}/{acme['id']}/{kind}"
    record = _post(api, url, {"name": "One"}).json()
    detail = f"{url}/{record['id']}"
    _post(api, f"{detail}/archive")
    _post(api, f"{CUSTOMERS}/{acme['id']}/archive")

    assert _names(api.get(url, {"archived": "1"})) == ["One"]
    assert _patch(api, detail, {"name": "Two"}).status_code == 400
    assert _post(api, f"{detail}/restore").status_code == 400
    assert _post(api, f"{detail}/archive").status_code == 400


def test_admin_cannot_delete_or_store_bad_seed_hints(client):
    from django.core.exceptions import ValidationError

    admin_user = get_user_model().objects.create_superuser("root", password="x")
    client.force_login(admin_user)
    acme = Customer.objects.create(name="Acme")
    site = Site.objects.for_customer(acme).create(name="HQ")
    assert client.get(f"/admin/collectors/site/{site.pk}/delete/").status_code == 403
    assert client.get(f"/admin/collectors/customer/{acme.pk}/delete/").status_code == 403

    site.seed_hints = ["$(id)"]
    with pytest.raises(ValidationError):
        site.full_clean()


@pytest.mark.parametrize(("perm", "allowed"), [("add", False), ("change", True)])
def test_archive_needs_change_permission(client, perm, allowed):
    acme = Customer.objects.create(name="Acme")
    user = get_user_model().objects.create_user("clerk", is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename=f"{perm}_customer"))
    client.force_login(user)
    status = _post(client, f"{CUSTOMERS}/{acme.pk}/archive").status_code
    assert status == (200 if allowed else 403)


def test_create_collector_reuses_a_customer_named_in_another_case(capsys):
    Customer.objects.create(name="Acme")
    call_command("create_collector", customer="acme", name="vm01")
    assert Customer.objects.count() == 1
    assert Customer.objects.get().collectors.count() == 1

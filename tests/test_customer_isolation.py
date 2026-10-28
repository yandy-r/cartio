"""No data, collector or credential crosses customers, enforced in the data layer (YAN-1649)."""

import json

import pytest
from cartio_controller.collectors.models import (
    CheckIn,
    Collector,
    CrossCustomerError,
    Customer,
    CustomerOwned,
    Engagement,
    Site,
    UnscopedQueryError,
)
from cartio_core.protocol import CHECKIN_PATH, bearer
from django.apps import apps

pytestmark = pytest.mark.django_db

OWNED = [model for model in apps.get_app_config("collectors").get_models() if model is not Customer]


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def globex():
    return Customer.objects.create(name="Globex")


def _collector(customer, name="vm01"):
    collector = Collector.objects.for_customer(customer).create(name=name)
    secret = collector.issue_secret()
    collector.save()
    return collector, secret


def _records(customer):
    """One record of every customer-owned kind for `customer`."""
    collector, _ = _collector(customer)
    return {
        Engagement: Engagement.objects.for_customer(customer).create(name="Audit"),
        Site: Site.objects.for_customer(customer).create(name="HQ"),
        Collector: collector,
        CheckIn: CheckIn.objects.for_customer(customer).create(
            collector=collector, hostname="vm01", version="1", platform="linux"
        ),
    }


def test_every_table_carries_a_customer():
    assert {model.__name__ for model in OWNED} == {"Engagement", "Site", "Collector", "CheckIn"}
    for model in OWNED:
        assert issubclass(model, CustomerOwned), model
        field = model._meta.get_field("customer")
        assert field.related_model is Customer
        assert not field.null


@pytest.mark.parametrize("model", OWNED, ids=lambda m: m.__name__)
def test_queries_must_name_a_customer(model, acme):
    for query in (
        lambda: model.objects.all(),
        lambda: model.objects.filter(pk=1),
        lambda: model.objects.get(pk=1),
        lambda: model.objects.count(),
        lambda: model.objects.create(customer=acme),
    ):
        with pytest.raises(UnscopedQueryError):
            query()


@pytest.mark.parametrize("model", OWNED, ids=lambda m: m.__name__)
def test_a_customer_scope_never_shows_another_customers_rows(model, acme, globex):
    mine = _records(acme)[model]
    theirs = _records(globex)[model]

    assert list(model.objects.for_customer(acme)) == [mine]
    assert list(model.objects.for_customer(globex)) == [theirs]
    assert not model.objects.for_customer(acme).filter(pk=theirs.pk).exists()
    # Chained querysets keep the scope.
    assert not model.objects.for_customer(acme).exclude(pk=mine.pk).filter(pk=theirs.pk)
    assert model.objects.for_customer(acme).filter(pk=theirs.pk).delete()[0] == 0
    # The explicit admin path sees both.
    assert set(model.all_customers.all()) == {mine, theirs}


@pytest.mark.parametrize("model", [Engagement, Site, Collector], ids=lambda m: m.__name__)
def test_scoped_create_pins_the_customer(model, acme, globex):
    scoped = model.objects.for_customer(acme)
    assert scoped.create(name="One").customer == acme
    with pytest.raises(CrossCustomerError):
        scoped.create(name="Two", customer=globex)
    with pytest.raises(CrossCustomerError):
        scoped.create(name="Three", customer_id=globex.pk)
    assert scoped.filter(name="Four").get_or_create(name="Four")[0].customer == acme
    assert not model.all_customers.filter(customer=globex).exists()


def test_scope_needs_a_saved_customer():
    with pytest.raises(UnscopedQueryError):
        Site.objects.for_customer(Customer(name="Unsaved"))
    with pytest.raises(UnscopedQueryError):
        Site.objects.for_customer(None)


def test_a_record_cannot_link_two_customers(acme, globex):
    acme_collector, _ = _collector(acme)
    with pytest.raises(CrossCustomerError):
        CheckIn(
            customer=globex, collector=acme_collector, hostname="x", version="1", platform="x"
        ).save()
    with pytest.raises(CrossCustomerError):
        CheckIn.objects.for_customer(globex).create(
            collector=acme_collector, hostname="x", version="1", platform="x"
        )

    # Moving a check-in to another customer's collector fails too.
    check_in = CheckIn.objects.for_customer(acme).create(
        collector=acme_collector, hostname="x", version="1", platform="x"
    )
    globex_collector, _ = _collector(globex)
    check_in.collector = globex_collector
    with pytest.raises(CrossCustomerError):
        check_in.save()
    assert not CheckIn.objects.for_customer(globex).exists()


def test_a_child_takes_its_parents_customer(acme):
    collector, _ = _collector(acme)
    check_in = CheckIn(collector=collector, hostname="x", version="1", platform="x")
    check_in.save()
    assert check_in.customer == acme


def test_a_collector_sees_only_its_own_customer(acme, globex):
    acme_records = _records(acme)
    globex_records = _records(globex)
    collector = acme_records[Collector]

    for model in OWNED:
        visible = set(collector.scoped(model))
        assert acme_records[model] in visible
        assert globex_records[model] not in visible
    with pytest.raises(CrossCustomerError):
        collector.scoped(Site).create(name="Branch", customer=globex)


def test_checkin_is_recorded_under_the_collectors_customer(client, acme, globex):
    _collector(globex, name="vm01")
    collector, secret = _collector(acme, name="vm02")

    response = client.post(
        CHECKIN_PATH,
        data=json.dumps({"hostname": "vm02", "version": "1", "platform": "linux"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=bearer(str(collector.id), secret),
    )
    assert response.status_code == 200
    stored = CheckIn.all_customers.get()
    assert stored.customer == acme
    assert stored.collector == collector
    assert Collector.all_customers.get(customer=globex).last_check_in is None


def test_a_collector_cannot_act_as_another_customers_collector(client, acme, globex):
    _, acme_secret = _collector(acme)
    globex_collector, _ = _collector(globex)

    response = client.post(
        CHECKIN_PATH,
        data=json.dumps({"hostname": "vm01", "version": "1", "platform": "linux"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=bearer(str(globex_collector.id), acme_secret),
    )
    assert response.status_code == 401
    assert not CheckIn.all_customers.exists()


def test_a_record_never_moves_to_another_customer(acme, globex):
    records = _records(acme)
    for model in (Engagement, Site, Collector):
        record = model.all_customers.get(pk=records[model].pk)
        record.customer = globex
        with pytest.raises(CrossCustomerError):
            record.save()
        with pytest.raises(CrossCustomerError):
            model.objects.for_customer(acme).update_or_create(
                pk=record.pk, defaults={"customer": globex}
            )
    assert not Collector.all_customers.filter(customer=globex).exists()


@pytest.mark.parametrize("manager", ["scoped", "all_customers"])
def test_bulk_writes_cannot_move_or_relink_records(manager, acme, globex):
    records = _records(acme)
    globex_collector, _ = _collector(globex, name="vm02")

    def rows(model):
        if manager == "scoped":
            return model.objects.for_customer(acme)
        return model.all_customers.all()

    for kwargs in ({"customer": globex}, {"customer_id": globex.pk}):
        with pytest.raises(CrossCustomerError):
            rows(Site).update(**kwargs)
    with pytest.raises(CrossCustomerError):
        rows(CheckIn).update(collector=globex_collector)
    site = records[Site]
    site.customer = globex
    with pytest.raises(CrossCustomerError):
        rows(Site).bulk_update([site], ["customer"])
    with pytest.raises(CrossCustomerError):
        rows(CheckIn).bulk_create(
            [CheckIn(customer=acme, collector=globex_collector, hostname="x", version="1")]
        )
    with pytest.raises(CrossCustomerError):
        globex.sites.add(Site.all_customers.get(pk=site.pk))

    assert set(Site.objects.for_customer(acme)) == {records[Site]}
    assert CheckIn.all_customers.get().collector == records[Collector]


def test_scoped_bulk_create_pins_the_customer(acme, globex):
    created = Site.objects.for_customer(acme).bulk_create([Site(name="A"), Site(name="B")])
    assert {site.customer_id for site in created} == {acme.pk}
    with pytest.raises(CrossCustomerError):
        Site.objects.for_customer(acme).bulk_create([Site(customer=globex, name="C")])
    with pytest.raises(UnscopedQueryError):
        Site.objects.bulk_create([Site(customer=acme, name="D")])


def test_scoped_create_accepts_the_same_customer_in_any_form(acme):
    assert (
        Site.objects.for_customer(acme).create(name="A", customer_id=str(acme.pk)).customer == acme
    )
    assert Site.objects.for_customer(str(acme.pk)).create(name="B", customer=acme).customer == acme


def test_a_record_loaded_or_built_without_its_customer_cannot_move(acme, globex):
    site = Site.objects.for_customer(acme).create(name="HQ")

    partial = Site.all_customers.only("name").get(pk=site.pk)
    partial.customer = globex
    with pytest.raises(CrossCustomerError):
        partial.save()

    with pytest.raises(CrossCustomerError):
        Site(pk=site.pk, customer=globex, name="HQ").save()

    # A save of unrelated fields doesn't make a pending customer change look saved.
    loaded = Site.all_customers.get(pk=site.pk)
    loaded.customer = globex
    loaded.save(update_fields=["name"])
    with pytest.raises(CrossCustomerError):
        loaded.save()
    assert Site.all_customers.get(pk=site.pk).customer == acme

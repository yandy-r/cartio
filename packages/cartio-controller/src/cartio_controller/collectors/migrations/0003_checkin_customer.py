import django.db.models.deletion
from django.db import migrations, models


def copy_customer_from_collector(apps, schema_editor):
    CheckIn = apps.get_model("collectors", "CheckIn")
    Collector = apps.get_model("collectors", "Collector")
    for collector in Collector.objects.all():
        CheckIn.objects.filter(collector=collector).update(customer_id=collector.customer_id)


class Migration(migrations.Migration):
    # The NOT NULL change is in 0004, its own transaction: PostgreSQL won't alter a table
    # with pending deferred FK checks from the backfill.

    dependencies = [
        ("collectors", "0002_customers_engagements_sites"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkin",
            name="customer",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)ss",
                to="collectors.customer",
            ),
        ),
        migrations.RunPython(copy_customer_from_collector, migrations.RunPython.noop),
    ]

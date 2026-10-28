import django.db.models.deletion
import django.db.models.manager
from django.db import migrations, models


def copy_customer_from_collector(apps, schema_editor):
    CheckIn = apps.get_model("collectors", "CheckIn")
    Collector = apps.get_model("collectors", "Collector")
    for collector in Collector.objects.all():
        CheckIn.objects.filter(collector=collector).update(customer_id=collector.customer_id)


class Migration(migrations.Migration):

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
        migrations.AlterField(
            model_name="checkin",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)ss",
                to="collectors.customer",
            ),
        ),
        migrations.AlterModelManagers(
            name="checkin",
            managers=[
                ("all_customers", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name="collector",
            managers=[
                ("all_customers", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name="engagement",
            managers=[
                ("all_customers", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name="site",
            managers=[
                ("all_customers", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterField(
            model_name="collector",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)ss",
                to="collectors.customer",
            ),
        ),
        migrations.AlterField(
            model_name="engagement",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)ss",
                to="collectors.customer",
            ),
        ),
        migrations.AlterField(
            model_name="site",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)ss",
                to="collectors.customer",
            ),
        ),
    ]

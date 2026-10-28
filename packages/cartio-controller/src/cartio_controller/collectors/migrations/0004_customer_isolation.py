import django.db.models.deletion
import django.db.models.manager
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collectors", "0003_checkin_customer"),
    ]

    operations = [
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
            migrations.AlterModelOptions(
            name="checkin",
            options={"base_manager_name": "all_customers", "get_latest_by": "at", "ordering": ["-at"]},
        ),
        migrations.AlterModelOptions(
            name="collector",
            options={"base_manager_name": "all_customers", "ordering": ["customer__name", "name"]},
        ),
        migrations.AlterModelOptions(
            name="engagement",
            options={
                "base_manager_name": "all_customers",
                "ordering": ["customer__name", "-start_date", "name"],
            },
        ),
        migrations.AlterModelOptions(
            name="site",
            options={"base_manager_name": "all_customers", "ordering": ["customer__name", "name"]},
        ),
    ]

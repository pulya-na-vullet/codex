from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workshop", "0026_order_parts_and_taxable"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="comment",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Внутренняя заметка по заказ-наряду, в печать клиенту не попадает.",
                verbose_name="Комментарий",
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workshop", "0023_software_version_pending_signature"),
    ]

    operations = [
        migrations.CreateModel(
            name="TvDisplaySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "mode",
                    models.CharField(
                        choices=[("ads", "Реклама"), ("crm", "Страница CRM")],
                        db_index=True,
                        default="ads",
                        max_length=16,
                        verbose_name="Режим ТВ",
                    ),
                ),
                ("crm_path", models.CharField(blank=True, default="/", max_length=255, verbose_name="Путь CRM на ТВ")),
                ("ads_index", models.PositiveIntegerField(default=0, verbose_name="Слайд рекламы")),
                ("rev", models.PositiveIntegerField(default=1, verbose_name="Версия состояния")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Экран ТВ в клиентской зоне",
                "verbose_name_plural": "Экран ТВ в клиентской зоне",
            },
        ),
    ]

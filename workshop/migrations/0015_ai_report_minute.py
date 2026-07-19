from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workshop", "0014_marketing_blast"),
    ]

    operations = [
        migrations.AddField(
            model_name="yandexaisettings",
            name="report_minute_msk",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Минута по Москве (0–59)",
                verbose_name="Минута отправки (МСК)",
            ),
        ),
        migrations.AlterField(
            model_name="yandexaisettings",
            name="report_hour_msk",
            field=models.PositiveSmallIntegerField(
                default=20,
                help_text="Час по Москве (0–23)",
                verbose_name="Час отправки (МСК)",
            ),
        ),
    ]

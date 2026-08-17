from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workshop", "0024_tv_display_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="tvdisplaysettings",
            name="crm_height",
            field=models.PositiveIntegerField(default=900, verbose_name="Высота окна CRM на Mac"),
        ),
        migrations.AddField(
            model_name="tvdisplaysettings",
            name="crm_width",
            field=models.PositiveIntegerField(default=1440, verbose_name="Ширина окна CRM на Mac"),
        ),
    ]

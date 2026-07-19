from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("workshop", "0013_max_status_notify_templates"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketingBlast",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name="Когда")),
                ("template_text", models.TextField(verbose_name="Текст шаблона")),
                ("username", models.CharField(blank=True, default="", max_length=64, verbose_name="Кто отправил")),
            ],
            options={
                "verbose_name": "Маркетинговая рассылка",
                "verbose_name_plural": "Маркетинговые рассылки",
                "ordering": ["-id"],
            },
        ),
        migrations.AddField(
            model_name="smslog",
            name="blast",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="logs",
                to="workshop.marketingblast",
                verbose_name="Рассылка",
            ),
        ),
    ]

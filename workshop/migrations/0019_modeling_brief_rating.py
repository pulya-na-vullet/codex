from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workshop", "0018_order_additive_services_and_tablet"),
    ]

    operations = [
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_pending",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Показать всплывашку со звёздами после выполнения",
                verbose_name="Нужна оценка менеджера",
            ),
        ),
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_score",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Оценка 1–5"),
        ),
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_comment",
            field=models.TextField(blank=True, default="", verbose_name="Комментарий к оценке"),
        ),
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_event_id",
            field=models.CharField(blank=True, default="", max_length=128, verbose_name="event_id оценки в HUB"),
        ),
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_sent_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Оценка отправлена"),
        ),
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_hub_avg",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=4,
                null=True,
                verbose_name="Средняя оценка дизайнера (с HUB)",
            ),
        ),
        migrations.AddField(
            model_name="modelingbrief",
            name="rating_hub_count",
            field=models.PositiveIntegerField(
                blank=True, null=True, verbose_name="Число оценок дизайнера (с HUB)"
            ),
        ),
    ]

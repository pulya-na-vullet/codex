from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("workshop", "0027_order_comment"),
    ]

    operations = [
        migrations.CreateModel(
            name="PerformerRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="Название роли")),
                (
                    "photos_required",
                    models.BooleanField(
                        default=True,
                        help_text="Если включено — без фотографий записаться нельзя. Для мастера по ноготочкам можно выключить, для электрика оставить.",
                        verbose_name="Фото обязательны при записи",
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="Активна")),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "verbose_name": "Роль исполнителя",
                "verbose_name_plural": "Роли исполнителей",
                "ordering": ["sort_order", "name", "id"],
            },
        ),
        migrations.CreateModel(
            name="PerformerBooking",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("booking_number", models.CharField(max_length=32, unique=True, verbose_name="Номер")),
                ("comment", models.TextField(blank=True, default="", verbose_name="Комментарий")),
                (
                    "status",
                    models.CharField(
                        choices=[("new", "Новая"), ("done", "Выполнена"), ("cancelled", "Отменена")],
                        db_index=True,
                        default="new",
                        max_length=20,
                        verbose_name="Статус",
                    ),
                ),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "client",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="performer_bookings",
                        to="workshop.client",
                        verbose_name="Клиент",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_performer_bookings",
                        to="workshop.staffuser",
                        verbose_name="Создал",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bookings",
                        to="workshop.performerrole",
                        verbose_name="Роль исполнителя",
                    ),
                ),
            ],
            options={
                "verbose_name": "Запись к исполнителю",
                "verbose_name_plural": "Записи к исполнителям",
                "ordering": ["-id"],
            },
        ),
        migrations.CreateModel(
            name="PerformerBookingPhoto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to="bookings/photos/%Y/%m/", verbose_name="Фото")),
                ("uploaded_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "booking",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="workshop.performerbooking",
                        verbose_name="Запись",
                    ),
                ),
            ],
            options={
                "verbose_name": "Фото к записи",
                "verbose_name_plural": "Фото к записям",
                "ordering": ["id"],
            },
        ),
    ]

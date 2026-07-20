import uuid
from dataclasses import dataclass
from secrets import token_hex

from django.db import transaction
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from .models import BotConversationState, Designer, HubBrief, HubBriefEvent


@dataclass
class BotReply:
    text: str


def _create_event(brief: HubBrief, event: str, message: str = "") -> None:
    payload = {
        "event_id": str(uuid.uuid4()),
        "event": event,
        "local_brief_id": brief.local_brief_id,
        "brief_id": brief.public_id,
        "designer_name": brief.designer.full_name if brief.designer else "",
        "designer_id": brief.designer.max_user_id if brief.designer else "",
        "eta": brief.eta,
        "message": message,
    }
    HubBriefEvent.objects.create(
        event_id=payload["event_id"],
        brief=brief,
        event=event,
        payload_json=payload,
        delivered_ok=False,
    )


def _normalize_bot_text(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").strip().split())


def _handle_registration(max_user_id: str, text: str) -> BotReply | None:
    text = _normalize_bot_text(text)
    if text == "Регистрация: Дизайнер":
        BotConversationState.objects.update_or_create(
            max_user_id=max_user_id,
            defaults={"state": BotConversationState.State.WAITING_FULL_NAME},
        )
        return BotReply("Введите ФИО.")

    try:
        state = BotConversationState.objects.get(max_user_id=max_user_id)
    except BotConversationState.DoesNotExist:
        return None

    if state.state == BotConversationState.State.WAITING_FULL_NAME:
        state.full_name = text
        state.state = BotConversationState.State.WAITING_SBP_PHONE
        state.save(update_fields=["full_name", "state", "updated_at"])
        return BotReply("Укажите телефон СБП.")

    if state.state == BotConversationState.State.WAITING_SBP_PHONE:
        state.sbp_phone = text
        state.state = BotConversationState.State.WAITING_EXPERIENCE
        state.save(update_fields=["sbp_phone", "state", "updated_at"])
        return BotReply("Опишите ваш опыт в 3D-моделировании.")

    if state.state == BotConversationState.State.WAITING_EXPERIENCE:
        state.experience_text = text
        state.state = BotConversationState.State.WAITING_PORTFOLIO
        state.save(update_fields=["experience_text", "state", "updated_at"])
        return BotReply("Пришлите ссылку на портфолио.")

    if state.state == BotConversationState.State.WAITING_PORTFOLIO:
        web_login = max_user_id
        plain_password = token_hex(4)
        Designer.objects.update_or_create(
            max_user_id=max_user_id,
            defaults={
                "full_name": state.full_name,
                "sbp_phone": state.sbp_phone,
                "experience_text": state.experience_text,
                "portfolio_url": text,
                "web_login": web_login,
                "web_password_hash": make_password(plain_password),
                "is_active": True,
            },
        )
        state.delete()
        return BotReply(
            "Регистрация завершена.\n"
            f"Веб-логин: {web_login}\n"
            f"Веб-пароль: {plain_password}\n"
            "Войдите в портал и откройте очередь задач."
        )

    return BotReply("Неизвестное состояние регистрации. Начните заново: Регистрация: Дизайнер")


def _format_brief_line(brief: HubBrief) -> str:
    return (
        f"{brief.public_id}: {brief.brief_number}, "
        f"цена {brief.agreed_price}, STL={'да' if brief.has_stl else 'нет'}, "
        f"скриншотов={brief.screenshots_count}"
    )


def process_bot_message(max_user_id: str, text: str) -> BotReply:
    text = _normalize_bot_text(text)
    registration_reply = _handle_registration(max_user_id=max_user_id, text=text)
    if registration_reply:
        return registration_reply

    try:
        designer = Designer.objects.get(max_user_id=max_user_id, is_active=True)
    except Designer.DoesNotExist:
        return BotReply("Сначала зарегистрируйтесь: Регистрация: Дизайнер")

    if text == "Очередь":
        queued = HubBrief.objects.filter(status=HubBrief.Status.QUEUED).order_by("created_at")[:20]
        if not queued:
            return BotReply("Сейчас нет доступных задач.")
        lines = ["Доступные задачи:"] + [_format_brief_line(brief) for brief in queued]
        return BotReply("\n".join(lines))

    if text.startswith("Беру "):
        payload = text.split(maxsplit=2)
        if len(payload) < 3:
            return BotReply("Формат: Беру <brief_id> <срок>")
        brief_id = payload[1]
        eta = payload[2]
        with transaction.atomic():
            try:
                brief = HubBrief.objects.select_for_update().get(public_id=brief_id)
            except HubBrief.DoesNotExist:
                return BotReply("Задача не найдена.")
            if brief.status != HubBrief.Status.QUEUED:
                return BotReply("Задача уже занята или недоступна.")
            brief.status = HubBrief.Status.ASSIGNED
            brief.designer = designer
            brief.eta = eta
            brief.save(update_fields=["status", "designer", "eta", "updated_at"])
            _create_event(brief, event="taken_in_work")
        return BotReply(f"Задача {brief_id} назначена на вас.")

    if text.startswith("Уточнение "):
        payload = text.split(maxsplit=2)
        if len(payload) < 3:
            return BotReply("Формат: Уточнение <brief_id> <текст>")
        brief_id = payload[1]
        message = payload[2]
        with transaction.atomic():
            try:
                brief = HubBrief.objects.select_for_update().get(public_id=brief_id, designer=designer)
            except HubBrief.DoesNotExist:
                return BotReply("Задача не найдена или не назначена вам.")
            brief.status = HubBrief.Status.NEEDS_CLARIFICATION
            brief.last_message = message
            brief.save(update_fields=["status", "last_message", "updated_at"])
            _create_event(brief, event="needs_clarification", message=message)
        return BotReply("Уточнение отправлено менеджеру.")

    if text.startswith("Готово "):
        payload = text.split(maxsplit=1)
        if len(payload) < 2:
            return BotReply("Формат: Готово <brief_id>")
        brief_id = payload[1]
        with transaction.atomic():
            try:
                brief = HubBrief.objects.select_for_update().get(public_id=brief_id, designer=designer)
            except HubBrief.DoesNotExist:
                return BotReply("Задача не найдена или не назначена вам.")
            brief.status = HubBrief.Status.DONE
            brief.done_at = timezone.now()
            brief.save(update_fields=["status", "done_at", "updated_at"])
            _create_event(brief, event="done")
        return BotReply(f"Задача {brief_id} отмечена как готовая.")

    return BotReply("Команды: Очередь | Беру <brief_id> <срок> | Уточнение <brief_id> <текст> | Готово <brief_id>")

import uuid
from functools import wraps

from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth import SiteAuthError, authenticate_site_request
from .designer_auth import (
    DesignerAuthError,
    authenticate_designer,
    authenticate_designer_credentials,
    create_designer_session,
)
from .models import Designer, HubBrief
from .serializers import (
    BriefInSerializer,
    BriefOutSerializer,
    ClaimBriefInSerializer,
    DesignerBriefOutSerializer,
    DesignerLoginInSerializer,
)
from .services import process_bot_message

DESIGNER_SESSION_KEY = "designer_id"


def _claim_brief_for_designer(*, designer, brief_id: str, eta: str):
    with transaction.atomic():
        brief = get_object_or_404(HubBrief.objects.select_for_update(), public_id=brief_id)
        if brief.status != HubBrief.Status.QUEUED:
            return None, {
                "detail": "Эту задачу уже взял другой дизайнер.",
                "status": brief.status,
                "designer_name": brief.designer.full_name if brief.designer else "",
            }
        brief.status = HubBrief.Status.ASSIGNED
        brief.designer = designer
        brief.eta = eta
        brief.save(update_fields=["status", "designer", "eta", "updated_at"])
        return brief, None


def _require_designer_session(view_func):
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        designer_id = request.session.get(DESIGNER_SESSION_KEY)
        if not designer_id:
            return redirect("designer-web-login")
        try:
            designer = Designer.objects.get(id=designer_id, is_active=True)
        except Designer.DoesNotExist:
            request.session.pop(DESIGNER_SESSION_KEY, None)
            return redirect("designer-web-login")
        request.designer = designer
        return view_func(request, *args, **kwargs)

    return _wrapped


class BriefListCreateView(APIView):
    def post(self, request):
        try:
            site = authenticate_site_request(request)
        except SiteAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = BriefInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        brief = HubBrief.objects.filter(site=site, local_brief_id=data["local_brief_id"]).first()
        if brief is None:
            brief = HubBrief.objects.create(
                public_id=str(uuid.uuid4()),
                site=site,
                local_brief_id=data["local_brief_id"],
                brief_number=data["brief_number"],
                client_ref=data["client_ref"],
                model_url=data.get("model_url", ""),
                description=data.get("description", ""),
                agreed_price=data["agreed_price"],
                designer_share_amount=data["designer_share_amount"],
                site_share_amount=data["site_share_amount"],
                has_stl=data.get("has_stl", False),
                screenshots_count=data.get("screenshots_count", 0),
                status=HubBrief.Status.QUEUED,
            )
        else:
            brief.brief_number = data["brief_number"]
            brief.client_ref = data["client_ref"]
            brief.model_url = data.get("model_url", "")
            brief.description = data.get("description", "")
            brief.agreed_price = data["agreed_price"]
            brief.designer_share_amount = data["designer_share_amount"]
            brief.site_share_amount = data["site_share_amount"]
            brief.has_stl = data.get("has_stl", False)
            brief.screenshots_count = data.get("screenshots_count", 0)
            brief.status = HubBrief.Status.QUEUED
            brief.save()
        return Response(
            {"brief_id": brief.public_id, "id": brief.public_id, "status": brief.status},
            status=status.HTTP_200_OK,
        )


class BriefDetailView(APIView):
    def get(self, request, brief_id: str):
        try:
            site = authenticate_site_request(request)
        except SiteAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        brief = get_object_or_404(HubBrief, public_id=brief_id, site=site)
        return Response(BriefOutSerializer(brief).data)

    def post(self, request, brief_id: str):
        try:
            site = authenticate_site_request(request)
        except SiteAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        brief = get_object_or_404(HubBrief, public_id=brief_id, site=site)
        serializer = BriefInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        brief.local_brief_id = data["local_brief_id"]
        brief.brief_number = data["brief_number"]
        brief.client_ref = data["client_ref"]
        brief.model_url = data.get("model_url", "")
        brief.description = data.get("description", "")
        brief.agreed_price = data["agreed_price"]
        brief.designer_share_amount = data["designer_share_amount"]
        brief.site_share_amount = data["site_share_amount"]
        brief.has_stl = data.get("has_stl", False)
        brief.screenshots_count = data.get("screenshots_count", 0)
        if brief.status == HubBrief.Status.NEEDS_CLARIFICATION:
            brief.status = HubBrief.Status.CLARIFICATION_PROVIDED
        brief.save()
        return Response(BriefOutSerializer(brief).data, status=status.HTTP_200_OK)


class BriefMessageView(APIView):
    def post(self, request, brief_id: str):
        try:
            site = authenticate_site_request(request)
        except SiteAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        brief = get_object_or_404(HubBrief, public_id=brief_id, site=site)
        text = request.data.get("text", "").strip()
        if not text:
            return Response({"detail": "text is required"}, status=status.HTTP_400_BAD_REQUEST)
        brief.last_message = text
        brief.status = HubBrief.Status.CLARIFICATION_PROVIDED
        brief.save(update_fields=["last_message", "status", "updated_at"])
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)


class MaxWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        from .max_bot import process_updates_payload, send_max_message
        from .models import MaxBotSettings

        cfg = MaxBotSettings.get_solo()
        token = (cfg.bot_token or "").strip()
        payload = request.data

        # Simplified manual/test format: {user_id, text}
        user_id = str(payload.get("user_id", "")).strip() if isinstance(payload, dict) else ""
        text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
        if user_id and text and not payload.get("update_type") and "updates" not in payload:
            reply = process_bot_message(max_user_id=user_id, text=text)
            if token:
                try:
                    send_max_message(token=token, user_id=user_id, text=reply.text)
                except Exception:
                    pass
            return Response({"reply": reply.text}, status=status.HTTP_200_OK)

        replies = process_updates_payload(
            payload if isinstance(payload, (dict, list)) else {},
            token=token,
            welcome_text=cfg.welcome_text or "",
        )
        if not replies:
            return Response({"detail": "no actionable updates"}, status=status.HTTP_200_OK)
        return Response({"replies": replies, "reply": replies[-1]}, status=status.HTTP_200_OK)


class DesignerLoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = DesignerLoginInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            designer, token = create_designer_session(login=data["login"], password=data["password"])
        except DesignerAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        return Response(
            {
                "token": token.key,
                "expires_at": token.expires_at.isoformat(),
                "designer": {"id": designer.id, "full_name": designer.full_name, "login": designer.web_login},
            },
            status=status.HTTP_200_OK,
        )


class DesignerBriefQueueView(APIView):
    def get(self, request):
        try:
            designer = authenticate_designer(request)
        except DesignerAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        briefs = (
            HubBrief.objects.select_related("designer", "site")
            .exclude(status=HubBrief.Status.DRAFT)
            .exclude(status=HubBrief.Status.CANCELLED)
            .order_by("-updated_at")
        )
        payload = DesignerBriefOutSerializer(briefs, many=True).data
        return Response(
            {
                "viewer": {"id": designer.id, "full_name": designer.full_name},
                "results": payload,
            },
            status=status.HTTP_200_OK,
        )


class DesignerBriefClaimView(APIView):
    def post(self, request, brief_id: str):
        try:
            designer = authenticate_designer(request)
        except DesignerAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = ClaimBriefInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        eta = serializer.validated_data["eta"]

        brief, error = _claim_brief_for_designer(designer=designer, brief_id=brief_id, eta=eta)
        if error:
            return Response(error, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                "brief_id": brief.public_id,
                "status": brief.status,
                "designer_name": designer.full_name,
                "eta": brief.eta,
            },
            status=status.HTTP_200_OK,
        )


def designer_login_page(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        login = request.POST.get("login", "").strip()
        password = request.POST.get("password", "")
        try:
            designer = authenticate_designer_credentials(login=login, password=password)
        except DesignerAuthError as exc:
            messages.error(request, str(exc))
            return render(request, "hub/designer_login.html", {"login_value": login})
        request.session[DESIGNER_SESSION_KEY] = designer.id
        return redirect("designer-web-queue")

    return render(request, "hub/designer_login.html")


@_require_designer_session
def designer_logout(request: HttpRequest) -> HttpResponse:
    request.session.pop(DESIGNER_SESSION_KEY, None)
    return redirect("designer-web-login")


@_require_designer_session
def designer_queue_page(request: HttpRequest) -> HttpResponse:
    briefs = (
        HubBrief.objects.select_related("designer", "site")
        .exclude(status=HubBrief.Status.DRAFT)
        .exclude(status=HubBrief.Status.CANCELLED)
        .order_by("-updated_at")
    )
    context = {
        "designer": request.designer,
        "queued_briefs": [brief for brief in briefs if brief.status == HubBrief.Status.QUEUED],
        "taken_briefs": [brief for brief in briefs if brief.status != HubBrief.Status.QUEUED],
    }
    return render(request, "hub/designer_queue.html", context)


@_require_designer_session
def designer_claim_page(request: HttpRequest, brief_id: str) -> HttpResponse:
    if request.method != "POST":
        return redirect("designer-web-queue")
    eta = request.POST.get("eta", "").strip()
    if not eta:
        messages.error(request, "Укажите срок выполнения.")
        return redirect("designer-web-queue")
    brief, error = _claim_brief_for_designer(designer=request.designer, brief_id=brief_id, eta=eta)
    if error:
        messages.error(
            request,
            f"{error['detail']} Исполнитель: {error['designer_name'] or 'не указан'}.",
        )
    else:
        messages.success(request, f"Задача {brief.brief_number} назначена на вас.")
    return redirect("designer-web-queue")

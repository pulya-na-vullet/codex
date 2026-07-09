from __future__ import annotations

import os
import time
from functools import wraps

from flask import Flask, flash, redirect, request, session, url_for


DEFAULT_USERNAME = "ITM"
DEFAULT_PASSWORD = "pass"
IDLE_TIMEOUT_SECONDS = 6 * 60 * 60  # 6 hours


def configure_auth(app: Flask) -> None:
    app.config.setdefault("AUTH_USERNAME", os.getenv("IT_MASTER_USER", DEFAULT_USERNAME))
    app.config.setdefault("AUTH_PASSWORD", os.getenv("IT_MASTER_PASSWORD", DEFAULT_PASSWORD))
    app.config.setdefault("AUTH_IDLE_SECONDS", int(os.getenv("IT_MASTER_IDLE_SECONDS", str(IDLE_TIMEOUT_SECONDS))))
    # Keep signed session cookies for at least the idle window.
    app.config["PERMANENT_SESSION_LIFETIME"] = app.config["AUTH_IDLE_SECONDS"]

    @app.before_request
    def require_login():
        endpoint = request.endpoint or ""
        if endpoint in {"login", "logout", "static"}:
            return None
        if endpoint.startswith("static"):
            return None

        if not session.get("authenticated"):
            if endpoint != "login":
                return redirect(url_for("login", next=request.path))
            return None

        now = time.time()
        last_active = float(session.get("last_active", now))
        idle_limit = int(app.config["AUTH_IDLE_SECONDS"])
        if now - last_active > idle_limit:
            session.clear()
            flash("Сессия завершена из‑за отсутствия активности (6 часов).", "warning")
            return redirect(url_for("login", next=request.path))

        session["last_active"] = now
        session.permanent = True
        return None


def attempt_login(app: Flask, username: str, password: str) -> bool:
    expected_user = str(app.config["AUTH_USERNAME"])
    expected_pass = str(app.config["AUTH_PASSWORD"])
    if username == expected_user and password == expected_pass:
        session.clear()
        session["authenticated"] = True
        session["username"] = username
        session["last_active"] = time.time()
        session.permanent = True
        return True
    return False


def logout_user() -> None:
    session.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped

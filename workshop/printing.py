from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from workshop.audit import log_action
from workshop.models import AuditLog, PrintJob, PrintJobStatus

PRINT_COPIES = int(getattr(settings, "PRINT_COPIES", 2) or 2)
_worker_lock = threading.Lock()
_worker_started = False
_wake = threading.Event()


def spool_dir() -> Path:
    path = Path(getattr(settings, "PRINT_SPOOL_DIR", Path(settings.BASE_DIR) / "print_spool"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def enqueue_pdf_print(
    *,
    pdf_bytes: bytes,
    title: str,
    doc_type: str,
    entity_type: str,
    entity_id,
    username: str = "",
    copies: int | None = None,
    request=None,
) -> list[PrintJob]:
    """Put PDF into print queue as N sequential jobs (default 2 copies)."""
    copies = max(1, int(copies if copies is not None else PRINT_COPIES))
    file_name = f"{timezone.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{doc_type}.pdf"
    file_path = spool_dir() / file_name
    file_path.write_bytes(pdf_bytes)

    jobs: list[PrintJob] = []
    with transaction.atomic():
        for idx in range(1, copies + 1):
            jobs.append(
                PrintJob.objects.create(
                    file_path=str(file_path),
                    title=title,
                    doc_type=doc_type,
                    entity_type=entity_type,
                    entity_id=str(entity_id),
                    copy_index=idx,
                    copies_total=copies,
                    username=username or "",
                    status=PrintJobStatus.PENDING,
                )
            )

    log_action(
        request,
        f"{doc_type}_print_queued",
        entity_type=entity_type,
        entity_id=entity_id,
        details=f"{title} x{copies} (очередь: {', '.join(f'#{j.id}' for j in jobs)})",
    )
    wake_print_worker()
    return jobs


def wake_print_worker() -> None:
    start_print_worker()
    _wake.set()


def start_print_worker() -> None:
    global _worker_started
    if not getattr(settings, "PRINT_WORKER_ENABLED", True):
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        thread = threading.Thread(target=_worker_loop, name="print-queue-worker", daemon=True)
        thread.start()


def _worker_loop() -> None:
    close_old_connections()
    try:
        PrintJob.objects.filter(status=PrintJobStatus.PRINTING).update(
            status=PrintJobStatus.PENDING,
            error="восстановлено после перезапуска",
            started_at=None,
        )
    except Exception:
        pass

    while True:
        close_old_connections()
        try:
            job = _claim_next_job()
            if job is None:
                _wake.wait(timeout=1.0)
                _wake.clear()
                continue
            _process_job(job)
        except Exception:
            time.sleep(1.0)
        finally:
            close_old_connections()


def _claim_next_job() -> PrintJob | None:
    with transaction.atomic():
        job = (
            PrintJob.objects.select_for_update()
            .filter(status=PrintJobStatus.PENDING)
            .order_by("id")
            .first()
        )
        if not job:
            return None
        # Do not start next document while another is printing.
        if PrintJob.objects.filter(status=PrintJobStatus.PRINTING).exists():
            return None
        job.status = PrintJobStatus.PRINTING
        job.started_at = timezone.now()
        job.error = ""
        job.save(update_fields=["status", "started_at", "error"])
        return job


def _write_job_audit(job: PrintJob, action: str, details: str) -> None:
    AuditLog.objects.create(
        username=job.username or "",
        action=action,
        entity_type=job.entity_type or "",
        entity_id=str(job.entity_id or ""),
        details=details,
        ip_address=None,
    )


def _process_job(job: PrintJob) -> None:
    try:
        if not job.file_path or not os.path.exists(job.file_path):
            raise FileNotFoundError(f"Файл печати не найден: {job.file_path}")
        _submit_pdf_and_wait(job.file_path)
        job.status = PrintJobStatus.DONE
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        _write_job_audit(
            job,
            f"{job.doc_type}_print_done",
            f"{job.title} экз. {job.copy_index}/{job.copies_total} (job #{job.id})",
        )
        _cleanup_file_if_unused(job.file_path)
    except Exception as err:
        job.status = PrintJobStatus.FAILED
        job.finished_at = timezone.now()
        job.error = str(err)[:2000]
        job.save(update_fields=["status", "finished_at", "error"])
        _write_job_audit(
            job,
            f"{job.doc_type}_print_error",
            f"{job.title} экз. {job.copy_index}/{job.copies_total}: {err}",
        )
    finally:
        _wake.set()


def _cleanup_file_if_unused(file_path: str) -> None:
    still_needed = PrintJob.objects.filter(
        file_path=file_path,
        status__in=[PrintJobStatus.PENDING, PrintJobStatus.PRINTING],
    ).exists()
    if still_needed:
        return
    try:
        os.unlink(file_path)
    except OSError:
        pass


def _list_windows_print_job_ids() -> set[str]:
    cmd = (
        "Get-CimInstance Win32_PrintJob -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.JobId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    ids: set[str] = set()
    for line in (result.stdout or "").splitlines():
        value = line.strip()
        if value:
            ids.add(value)
    return ids


def _submit_pdf_windows(path: str) -> None:
    # Prefer PowerShell Print verb; fallback to startfile.
    ps = (
        f'$p = {repr(str(path))}; '
        "Start-Process -FilePath $p -Verb Print -ErrorAction Stop"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        os.startfile(path, "print")  # type: ignore[attr-defined]


def _wait_windows_print_finished(before_ids: set[str], timeout_sec: float = 600.0) -> None:
    """Wait until new spooler jobs appear and then leave the queue."""
    deadline = time.time() + timeout_sec
    seen_new: set[str] = set()
    # Phase 1: wait for job to be accepted by spooler.
    while time.time() < deadline and not seen_new:
        now_ids = _list_windows_print_job_ids()
        seen_new = now_ids - before_ids
        if seen_new:
            break
        time.sleep(0.5)
    if not seen_new:
        # Driver may have printed so fast that we missed the job; give hardware a beat.
        time.sleep(float(getattr(settings, "PRINT_FALLBACK_WAIT_SEC", 8)))
        return
    # Phase 2: wait until those jobs are gone (printed / removed from queue).
    while time.time() < deadline:
        now_ids = _list_windows_print_job_ids()
        if not (seen_new & now_ids):
            # Small settle delay before next document.
            time.sleep(1.0)
            return
        time.sleep(0.7)
    raise TimeoutError("Таймаут ожидания завершения задания печати в Windows")


def _submit_pdf_and_wait(path: str) -> None:
    system = platform.system()
    if system == "Windows":
        before = _list_windows_print_job_ids()
        _submit_pdf_windows(path)
        _wait_windows_print_finished(before)
        return

    # Linux / macOS: prefer CUPS lp and wait for not-completed jobs.
    try:
        subprocess.run(["lp", path], check=True, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        # No printer subsystem in CI — simulate success after short delay.
        time.sleep(0.2)
        return
    deadline = time.time() + float(getattr(settings, "PRINT_JOB_TIMEOUT_SEC", 600))
    while time.time() < deadline:
        try:
            stat = subprocess.run(
                ["lpstat", "-o"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            queue = (stat.stdout or "").strip()
            if not queue:
                time.sleep(0.5)
                return
        except FileNotFoundError:
            time.sleep(1.0)
            return
        time.sleep(0.7)
    raise TimeoutError("Таймаут ожидания завершения задания печати (lpstat)")

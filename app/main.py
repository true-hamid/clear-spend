import io
import logging
import os
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import login_required

from app import db
from app.excel_builder import (
    ProcessedTransaction,
    build_review_csv,
    build_workbook,
    flag_duplicates,
    recalculate_with_libreoffice,
)
from app.matching import MatchingEngine, record_unrecognized
from app.pdf_parser import parse_pdf

main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

DOWNLOAD_TTL = timedelta(minutes=30)
_pending_downloads: dict[str, dict] = {}
_pending_lock = threading.Lock()


def _sweep_expired_downloads():
    now = datetime.now(timezone.utc)
    with _pending_lock:
        expired = [token for token, entry in _pending_downloads.items() if entry["expires_at"] < now]
        for token in expired:
            _pending_downloads.pop(token)


@main_bp.route("/")
@login_required
def index():
    return redirect(url_for("main.upload"))


@main_bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    _sweep_expired_downloads()

    if request.method == "GET":
        return render_template("upload.html")

    files = [f for f in request.files.getlist("statements") if f and f.filename]
    if not files:
        flash("Please choose at least one PDF statement to upload.", "error")
        return redirect(url_for("main.upload"))

    non_pdf = [f.filename for f in files if not f.filename.lower().endswith(".pdf")]
    if non_pdf:
        flash(f"Only PDF files are accepted. Rejected: {', '.join(non_pdf)}", "error")
        return redirect(url_for("main.upload"))

    temp_input_paths = []
    all_raw = []
    try:
        for f in files:
            fd, temp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            f.save(temp_path)
            temp_input_paths.append(temp_path)
            try:
                all_raw.extend(parse_pdf(temp_path, f.filename))
            except Exception:
                logger.exception("Failed to parse %s", f.filename)
                flash(f"Could not parse '{f.filename}' — it may not be a text-based statement PDF.", "error")
    finally:
        for p in temp_input_paths:
            try:
                os.remove(p)
            except OSError:
                pass

    if not all_raw:
        flash("No transaction rows were found in the uploaded PDF(s).", "error")
        return redirect(url_for("main.upload"))

    engine = MatchingEngine()
    processed = []
    unrecognized_count = 0
    for raw in all_raw:
        if raw.category_override:
            # No merchant detail to match against (e.g. a Deal Summary/EMI
            # ledger row) — skip MatchingEngine and the Needs Review queue
            # entirely rather than flagging it as unrecognized.
            cleaned_name = raw.description.title()
            category = raw.category_override
            matched = True
        else:
            result = engine.match(raw.description)
            cleaned_name = result.cleaned_name
            category = result.category
            matched = result.matched
            if not matched:
                unrecognized_count += 1
                record_unrecognized(raw.description, raw.amount)
        processed.append(
            ProcessedTransaction(
                raw=raw,
                cleaned_name=cleaned_name,
                category=category,
                matched=matched,
            )
        )
    db.session.commit()

    flag_duplicates(processed)

    from app.models import CleanedMerchant, MerchantMapping

    mapping_rows = [
        {
            "original_description": m.original_description,
            "cleaned_name": m.cleaned_merchant.name,
            "category": m.cleaned_merchant.category,
        }
        for m in MerchantMapping.query.join(CleanedMerchant).order_by(CleanedMerchant.name).all()
    ]

    wb = build_workbook(processed, mapping_rows)

    # A real file on disk is only needed transiently, for the LibreOffice
    # recalculation subprocess (it requires a path, not a stream). Once
    # that finishes, the bytes are read into memory and the temp file is
    # removed immediately — well before any response streaming starts —
    # so nothing later depends on the file still existing. This avoids
    # deleting the file out from under an in-flight download response
    # (after_this_request runs before send_file's body is streamed to the
    # client, so deleting a file it still needs there is a race — and a
    # hard failure on Windows, where an open file can't be removed at all).
    fd, out_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(out_path)
        recalculated = recalculate_with_libreoffice(out_path)
        with open(out_path, "rb") as fh:
            workbook_bytes = fh.read()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    token = secrets.token_urlsafe(24)
    with _pending_lock:
        _pending_downloads[token] = {
            "data": workbook_bytes,
            "filename": f"ClearSpend_Export_{timestamp}.xlsx",
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "expires_at": datetime.now(timezone.utc) + DOWNLOAD_TTL,
        }
    session["download_token"] = token

    review_csv = build_review_csv(processed)
    review_token = None
    if review_csv is not None:
        review_token = secrets.token_urlsafe(24)
        with _pending_lock:
            _pending_downloads[review_token] = {
                "data": review_csv.encode("utf-8"),
                "filename": f"ClearSpend_NeedsReview_{timestamp}.csv",
                "mimetype": "text/csv",
                "expires_at": datetime.now(timezone.utc) + DOWNLOAD_TTL,
            }

    stats = {
        "total": len(processed),
        "matched": len(processed) - unrecognized_count,
        "unrecognized": unrecognized_count,
        "duplicates": sum(1 for p in processed if p.possible_duplicate),
        "recalculated": recalculated,
    }
    return render_template("summary.html", stats=stats, token=token, review_token=review_token)


@main_bp.route("/download/<token>")
@login_required
def download(token):
    _sweep_expired_downloads()
    with _pending_lock:
        entry = _pending_downloads.pop(token, None)
        if not entry:
            abort(404)

    return send_file(
        io.BytesIO(entry["data"]),
        as_attachment=True,
        download_name=entry["filename"],
        mimetype=entry["mimetype"],
    )

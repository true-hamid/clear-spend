import csv
import io
from functools import wraps

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash

from app import db
from app.models import CleanedMerchant, MerchantMapping, UnrecognizedMerchant, User

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _resolve_unrecognized(original_description: str) -> None:
    existing = UnrecognizedMerchant.query.filter_by(original_description=original_description).first()
    if existing:
        existing.status = "resolved"


# --- Mapping Manager ---------------------------------------------------


@admin_bp.route("/mappings")
@admin_required
def mappings():
    q = request.args.get("q", "").strip()
    query = MerchantMapping.query.join(CleanedMerchant)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                MerchantMapping.original_description.ilike(like),
                CleanedMerchant.name.ilike(like),
                CleanedMerchant.category.ilike(like),
            )
        )
    rows = query.order_by(CleanedMerchant.name).all()
    categories = sorted({c.category for c in CleanedMerchant.query.all()})
    return render_template("admin/mappings.html", rows=rows, categories=categories, q=q)


@admin_bp.route("/mappings/add", methods=["POST"])
@admin_required
def mappings_add():
    original_description = request.form.get("original_description", "").strip()
    cleaned_name = request.form.get("cleaned_name", "").strip()
    category = request.form.get("category", "").strip()
    if not (original_description and cleaned_name and category):
        flash("All fields are required to add a mapping.", "error")
        return redirect(url_for("admin.mappings"))

    existing = MerchantMapping.query.filter(
        db.func.lower(MerchantMapping.original_description) == original_description.lower()
    ).first()
    if existing:
        flash("A mapping for that original description already exists.", "error")
        return redirect(url_for("admin.mappings"))

    merchant = CleanedMerchant.find_or_create(cleaned_name, category, updated_by=current_user.id)
    db.session.add(
        MerchantMapping(
            original_description=original_description,
            cleaned_merchant=merchant,
            updated_by=current_user.id,
        )
    )
    _resolve_unrecognized(original_description)
    db.session.commit()
    flash(f"Added mapping for '{original_description}'.", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/<int:mapping_id>/edit", methods=["POST"])
@admin_required
def mappings_edit(mapping_id):
    row = db.session.get(MerchantMapping, mapping_id) or abort(404)
    cleaned_name = request.form.get("cleaned_name", row.cleaned_merchant.name).strip()
    category = request.form.get("category", row.cleaned_merchant.category).strip()
    row.cleaned_merchant = CleanedMerchant.find_or_create(cleaned_name, category, updated_by=current_user.id)
    row.updated_by = current_user.id
    db.session.commit()
    flash(f"Updated mapping for '{row.original_description}'.", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/<int:mapping_id>/delete", methods=["POST"])
@admin_required
def mappings_delete(mapping_id):
    row = db.session.get(MerchantMapping, mapping_id) or abort(404)
    description = row.original_description
    db.session.delete(row)
    db.session.commit()
    flash(f"Deleted mapping for '{description}'.", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/bulk-category", methods=["POST"])
@admin_required
def mappings_bulk_category():
    ids = request.form.getlist("mapping_ids")
    new_category = request.form.get("new_category", "").strip()
    if not ids or not new_category:
        flash("Select at least one row and a category to bulk-assign.", "error")
        return redirect(url_for("admin.mappings"))
    merchant_ids = {
        r.cleaned_merchant_id for r in MerchantMapping.query.filter(MerchantMapping.id.in_(ids))
    }
    CleanedMerchant.query.filter(CleanedMerchant.id.in_(merchant_ids)).update(
        {"category": new_category, "updated_by": current_user.id}, synchronize_session=False
    )
    db.session.commit()
    flash(f"Assigned '{new_category}' to {len(ids)} row(s).", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/rename-category", methods=["POST"])
@admin_required
def mappings_rename_category():
    old_category = request.form.get("old_category", "").strip()
    new_category = request.form.get("new_category_name", "").strip()
    if not old_category or not new_category:
        flash("Both the category to rename and its new name are required.", "error")
        return redirect(url_for("admin.mappings"))
    count = CleanedMerchant.query.filter_by(category=old_category).update(
        {"category": new_category, "updated_by": current_user.id}, synchronize_session=False
    )
    db.session.commit()
    flash(f"Renamed category '{old_category}' to '{new_category}' on {count} merchant(s).", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/export")
@admin_required
def mappings_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["original_description", "cleaned_name", "category"])
    for row in MerchantMapping.query.join(CleanedMerchant).order_by(CleanedMerchant.name).all():
        writer.writerow([row.original_description, row.cleaned_merchant.name, row.cleaned_merchant.category])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=merchant_mapping_export.csv"},
    )


@admin_bp.route("/mappings/import", methods=["POST"])
@admin_required
def mappings_import():
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("admin.mappings"))

    stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    upserted = 0
    for row in reader:
        original_description = (row.get("original_description") or "").strip()
        cleaned_name = (row.get("cleaned_name") or "").strip()
        category = (row.get("category") or "").strip()
        if not (original_description and cleaned_name and category):
            continue
        merchant = CleanedMerchant.find_or_create(cleaned_name, category, updated_by=current_user.id)
        existing = MerchantMapping.query.filter(
            db.func.lower(MerchantMapping.original_description) == original_description.lower()
        ).first()
        if existing:
            existing.cleaned_merchant = merchant
            existing.updated_by = current_user.id
        else:
            db.session.add(
                MerchantMapping(
                    original_description=original_description,
                    cleaned_merchant=merchant,
                    updated_by=current_user.id,
                )
            )
        _resolve_unrecognized(original_description)
        upserted += 1
    db.session.commit()
    flash(f"Imported/updated {upserted} mapping row(s).", "success")
    return redirect(url_for("admin.mappings"))


# --- Unrecognized Merchants queue --------------------------------------


@admin_bp.route("/unrecognized")
@admin_required
def unrecognized():
    sort = request.args.get("sort", "total_amount")
    sort_col = UnrecognizedMerchant.total_amount if sort == "total_amount" else UnrecognizedMerchant.occurrence_count
    rows = (
        UnrecognizedMerchant.query.filter(UnrecognizedMerchant.status.in_(["pending", "exported"]))
        .order_by(sort_col.desc())
        .all()
    )
    return render_template("admin/unrecognized.html", rows=rows, sort=sort)


@admin_bp.route("/unrecognized/export")
@admin_required
def unrecognized_export():
    rows = UnrecognizedMerchant.query.filter_by(status="pending").order_by(
        UnrecognizedMerchant.total_amount.desc()
    ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["original_description", "occurrence_count", "total_amount", "first_seen_at"])
    for row in rows:
        writer.writerow([row.original_description, row.occurrence_count, row.total_amount, row.first_seen_at])
        row.status = "exported"
    db.session.commit()
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=unrecognized_merchants_export.csv"},
    )


@admin_bp.route("/unrecognized/import", methods=["POST"])
@admin_required
def unrecognized_import():
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Choose a CSV file (original_description, cleaned_name, category) to import.", "error")
        return redirect(url_for("admin.unrecognized"))

    stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    resolved = 0
    for row in reader:
        original_description = (row.get("original_description") or "").strip()
        cleaned_name = (row.get("cleaned_name") or "").strip()
        category = (row.get("category") or "").strip()
        if not (original_description and cleaned_name and category):
            continue
        merchant = CleanedMerchant.find_or_create(cleaned_name, category, updated_by=current_user.id)
        existing = MerchantMapping.query.filter(
            db.func.lower(MerchantMapping.original_description) == original_description.lower()
        ).first()
        if existing:
            existing.cleaned_merchant = merchant
            existing.updated_by = current_user.id
        else:
            db.session.add(
                MerchantMapping(
                    original_description=original_description,
                    cleaned_merchant=merchant,
                    updated_by=current_user.id,
                )
            )
        _resolve_unrecognized(original_description)
        resolved += 1
    db.session.commit()
    flash(f"Resolved {resolved} unrecognized merchant(s) into the mapping table.", "success")
    return redirect(url_for("admin.unrecognized"))


@admin_bp.route("/unrecognized/<int:row_id>/delete", methods=["POST"])
@admin_required
def unrecognized_delete(row_id):
    row = db.session.get(UnrecognizedMerchant, row_id) or abort(404)
    db.session.delete(row)
    db.session.commit()
    flash("Removed from the unrecognized merchants queue.", "success")
    return redirect(url_for("admin.unrecognized"))


# --- User management -----------------------------------------------------
# In-app account creation is deliberately restricted to the 'user' role —
# creating admin accounts stays CLI-only (manage.py create-user) per
# docs/auth.md.


@admin_bp.route("/users")
@admin_required
def users():
    rows = User.query.order_by(User.username).all()
    return render_template("admin/users.html", rows=rows)


@admin_bp.route("/users/create", methods=["POST"])
@admin_required
def users_create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("admin.users"))
    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("admin.users"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("admin.users"))

    existing = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if existing:
        flash(f"A user named '{username}' already exists.", "error")
        return redirect(url_for("admin.users"))

    user = User(username=username, password_hash=generate_password_hash(password), role="user")
    db.session.add(user)
    db.session.commit()
    flash(f"Created user '{username}'.", "success")
    return redirect(url_for("admin.users"))

from datetime import datetime, timezone

from flask_login import UserMixin

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin' | 'user'
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class CleanedMerchant(db.Model):
    """A canonical merchant identity. Many `MerchantMapping` rows (one per
    distinct original statement description, e.g. three different Carrefour
    branch strings) point at the same `CleanedMerchant` row, so renaming a
    merchant or fixing its category is a single-row update instead of a
    fan-out across every mapping that refers to it."""

    __tablename__ = "cleaned_merchants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    @classmethod
    def find_or_create(cls, name: str, category: str, updated_by=None) -> "CleanedMerchant":
        existing = cls.query.filter(db.func.lower(cls.name) == name.lower()).first()
        if existing:
            if existing.category != category:
                existing.category = category
                existing.updated_by = updated_by
            return existing
        merchant = cls(name=name, category=category, updated_by=updated_by)
        db.session.add(merchant)
        db.session.flush()
        return merchant


class MerchantMapping(db.Model):
    __tablename__ = "merchant_mapping"

    id = db.Column(db.Integer, primary_key=True)
    original_description = db.Column(db.String(255), unique=True, nullable=False)
    cleaned_merchant_id = db.Column(db.Integer, db.ForeignKey("cleaned_merchants.id"), nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    cleaned_merchant = db.relationship("CleanedMerchant", backref="mappings")


class UnrecognizedMerchant(db.Model):
    __tablename__ = "unrecognized_merchants"

    id = db.Column(db.Integer, primary_key=True)
    original_description = db.Column(db.String(255), unique=True, nullable=False)
    first_seen_at = db.Column(db.DateTime, default=utcnow)
    last_seen_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    occurrence_count = db.Column(db.Integer, default=1, nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending|exported|resolved

"""Deterministic merchant matching: exact match, then keyword/substring fuzzy
match, then fall back to a light auto-clean + Uncategorized / Other.
See build_instructions.md Section 2 and docs/mapping.md.
"""
import re
from dataclasses import dataclass
from decimal import Decimal

from app import db
from app.models import MerchantMapping, UnrecognizedMerchant, utcnow

UNCATEGORIZED = "Uncategorized / Other"

NOISE_SUFFIXES = (
    "DUBAI ARE",
    "ABU DHABI ARE",
    "AL AIN ARE",
    "SHARJAH ARE",
    "RAS AL KHAIMA ARE",
    "RAS AL KHAIMAH ARE",
    "FUJAIRAH ARE",
    "AJMAN ARE",
)
TRAILING_ARE_RE = re.compile(r"\s+ARE\s*$", re.IGNORECASE)
PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)\s*")
NON_ALNUM_RE = re.compile(r"[^A-Z0-9& ]+")
MULTISPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return MULTISPACE_RE.sub(" ", text.strip().upper())


def strip_noise(text: str) -> str:
    t = normalize(text)
    for suffix in NOISE_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
            break
    else:
        t = TRAILING_ARE_RE.sub("", t).strip()
    return t


def keyword_for(cleaned_name: str) -> str:
    """The matchable keyword derived from a mapping row's cleaned_name:
    strip parenthetical annotations, uppercase, strip non-alphanumeric noise."""
    t = PARENTHETICAL_RE.sub(" ", cleaned_name).strip()
    t = normalize(t)
    t = NON_ALNUM_RE.sub("", t)
    return MULTISPACE_RE.sub(" ", t).strip()


def auto_clean(description: str) -> str:
    stripped = strip_noise(description)
    return stripped.title() if stripped else description.strip().title()


@dataclass
class MatchResult:
    cleaned_name: str
    category: str
    matched: bool


class MatchingEngine:
    def __init__(self):
        rows = MerchantMapping.query.options(db.joinedload(MerchantMapping.cleaned_merchant)).all()
        self._exact = {normalize(r.original_description): r for r in rows}
        # (keyword, keyword_len, row) sorted longest-first so the most
        # specific keyword wins (e.g. "NOON MINUTES" before "NOON").
        candidates = []
        for r in rows:
            kw = keyword_for(r.cleaned_merchant.name)
            if kw:
                candidates.append((kw, len(kw), r))
        self._fuzzy = sorted(candidates, key=lambda c: c[1], reverse=True)

    def match(self, description: str) -> MatchResult:
        exact = self._exact.get(normalize(description))
        if exact:
            return MatchResult(exact.cleaned_merchant.name, exact.cleaned_merchant.category, True)

        stripped = strip_noise(description)
        for keyword, _, row in self._fuzzy:
            if keyword and keyword in stripped:
                return MatchResult(row.cleaned_merchant.name, row.cleaned_merchant.category, True)

        return MatchResult(auto_clean(description), UNCATEGORIZED, False)


def record_unrecognized(original_description: str, amount: float) -> None:
    """Upsert into unrecognized_merchants for an unmatched transaction."""
    existing = UnrecognizedMerchant.query.filter_by(
        original_description=original_description
    ).first()
    if existing:
        existing.occurrence_count += 1
        # total_amount round-trips through SQLAlchemy as decimal.Decimal
        # (Numeric column) — Decimal + float raises TypeError, so route the
        # incoming float through str() first to avoid binary-float noise.
        existing.total_amount = (existing.total_amount or Decimal("0")) + Decimal(str(amount))
        existing.last_seen_at = utcnow()
        if existing.status == "exported":
            # seen again after being exported but before being resolved;
            # keep it visible as still-outstanding work.
            existing.status = "pending"
    else:
        db.session.add(
            UnrecognizedMerchant(
                original_description=original_description,
                occurrence_count=1,
                total_amount=amount,
                status="pending",
            )
        )

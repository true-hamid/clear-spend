from app import db
from app.matching import (
    MatchingEngine,
    UNCATEGORIZED,
    auto_clean,
    keyword_for,
    normalize,
    record_unrecognized,
    strip_noise,
)
from app.models import UnrecognizedMerchant


def test_normalize_trims_and_uppercases():
    assert normalize("  nando's  cafe  ") == "NANDO'S CAFE"


def test_strip_noise_removes_known_city_suffix():
    assert strip_noise("CARREFOUR-MIRDIF CC DUBAI ARE") == "CARREFOUR-MIRDIF CC"
    assert strip_noise("SOME MERCHANT ABU DHABI ARE") == "SOME MERCHANT"
    assert strip_noise("AL AIN FOOD AND BEVERAG AL AIN ARE") == "AL AIN FOOD AND BEVERAG"


def test_strip_noise_falls_back_to_generic_trailing_are():
    # No exact suffix from NOISE_SUFFIXES matches "FUJAIRAH ARE" isn't in the
    # description here, but a generic trailing "ARE" should still be stripped.
    assert strip_noise("SOME MERCHANT XYZ ARE") == "SOME MERCHANT XYZ"


def test_keyword_for_strips_parentheticals_and_punctuation():
    assert keyword_for("Nando's") == "NANDOS"
    assert keyword_for("Noon Minutes (Quick Grocery Delivery)") == "NOON MINUTES"
    assert keyword_for("R&B") == "R&B"


def test_auto_clean_title_cases_unmatched_description():
    assert auto_clean("SOME UNKNOWN MERCHANT XYZ DUBAI ARE") == "Some Unknown Merchant Xyz"


class TestMatchingEngine:
    def test_exact_match_wins_over_fuzzy(self, app, seeded_mapping):
        with app.app_context():
            engine = MatchingEngine()
            result = engine.match("CARREFOUR-MIRDIF CC DUBAI ARE")
            assert result.matched is True
            assert result.cleaned_name == "Carrefour"
            assert result.category == "Groceries"

    def test_exact_match_is_case_insensitive(self, app, seeded_mapping):
        with app.app_context():
            engine = MatchingEngine()
            result = engine.match("carrefour-mirdif cc dubai are")
            assert result.matched is True
            assert result.cleaned_name == "Carrefour"

    def test_fuzzy_match_on_unseen_variant(self, app, seeded_mapping):
        with app.app_context():
            engine = MatchingEngine()
            # Not literally in the seed CSV, but "CARREFOUR" keyword should
            # still catch it via strip_noise + substring match.
            result = engine.match("CARREFOUR EXPRESS JLT DUBAI ARE")
            assert result.matched is True
            assert result.cleaned_name == "Carrefour"
            assert result.category == "Groceries"

    def test_longest_keyword_wins_over_shorter_prefix(self, app):
        # Deliberately construct two mapping rows whose keywords overlap as
        # prefixes of each other, to isolate the longest-keyword-wins rule
        # from the seed data (which has no such literal prefix conflict).
        from app.models import CleanedMerchant, MerchantMapping

        with app.app_context():
            db.session.add(
                MerchantMapping(
                    original_description="NOON DUBAI ARE",
                    cleaned_merchant=CleanedMerchant.find_or_create("Noon", "Dining & Food Delivery"),
                )
            )
            db.session.add(
                MerchantMapping(
                    original_description="NOON MINUTES LLC DUBAI ARE",
                    cleaned_merchant=CleanedMerchant.find_or_create("Noon Minutes", "Groceries"),
                )
            )
            db.session.commit()

            engine = MatchingEngine()
            # Neither of these descriptions is a literal seed row, so both
            # must resolve via the fuzzy/keyword path.
            result = engine.match("NOON MINUTES EXPRESS JBR DUBAI ARE")
            assert result.matched is True
            assert result.cleaned_name == "Noon Minutes"
            assert result.category == "Groceries"

    def test_no_match_falls_back_to_uncategorized(self, app, seeded_mapping):
        with app.app_context():
            engine = MatchingEngine()
            result = engine.match("TOTALLY UNKNOWN MERCHANT XYZ DUBAI ARE")
            assert result.matched is False
            assert result.category == UNCATEGORIZED
            assert result.cleaned_name == "Totally Unknown Merchant Xyz"

    def test_installment_plan_row_matches_seed_mapping_exactly(self, app, seeded_mapping):
        # The real sample statement's Installment Plan section produces
        # exactly this description (see app/pdf_parser.py) — confirm it
        # round-trips through the exact-match path, not just fuzzy.
        with app.app_context():
            engine = MatchingEngine()
            result = engine.match("INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY")
            assert result.matched is True
            assert result.category == "Home Services & Laundry"


class TestRecordUnrecognized:
    def test_creates_new_row_on_first_occurrence(self, app):
        with app.app_context():
            record_unrecognized("NEW MERCHANT XYZ", 50.0)
            db.session.commit()
            row = UnrecognizedMerchant.query.filter_by(original_description="NEW MERCHANT XYZ").first()
            assert row is not None
            assert row.occurrence_count == 1
            assert float(row.total_amount) == 50.0
            assert row.status == "pending"

    def test_bumps_count_and_sums_amount_on_repeat(self, app):
        with app.app_context():
            record_unrecognized("REPEAT MERCHANT", 10.0)
            record_unrecognized("REPEAT MERCHANT", 15.0)
            db.session.commit()
            row = UnrecognizedMerchant.query.filter_by(original_description="REPEAT MERCHANT").first()
            assert row.occurrence_count == 2
            assert float(row.total_amount) == 25.0

    def test_exported_row_flips_back_to_pending_on_reoccurrence(self, app):
        with app.app_context():
            record_unrecognized("EXPORTED MERCHANT", 10.0)
            db.session.commit()
            row = UnrecognizedMerchant.query.filter_by(original_description="EXPORTED MERCHANT").first()
            row.status = "exported"
            db.session.commit()

            record_unrecognized("EXPORTED MERCHANT", 5.0)
            db.session.commit()
            row = UnrecognizedMerchant.query.filter_by(original_description="EXPORTED MERCHANT").first()
            assert row.status == "pending"
            assert row.occurrence_count == 2

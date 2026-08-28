# Merchant matching engine + Mapping Manager

**Implementation**: `app/matching.py` (engine), `app/admin.py` (Mapping
Manager routes), `app/templates/admin/mappings.html` (UI).

## Schema

`merchant_mapping` is many-to-one onto `cleaned_merchants`
(`app/models.py`): each row is one distinct original statement
description (`original_description`, unique) with a `cleaned_merchant_id`
FK. `cleaned_merchants` holds the canonical `name` + `category` once, so
e.g. three different Carrefour branch strings each get their own
`merchant_mapping` row but share a single `cleaned_merchants` row —
renaming the merchant or fixing its category is a one-row update instead
of a fan-out, and two mapping rows can't drift to slightly different
spellings/casing for what's meant to be the same merchant.
`CleanedMerchant.find_or_create()` is the single place that resolves a
typed `cleaned_name` to a canonical row (case-insensitive match on
`name`), used by every write path below.

## Matching order (per transaction description)

1. **Exact match** against `merchant_mapping.original_description`,
   case-insensitive and whitespace-normalized (`MatchingEngine._exact`,
   keyed by `normalize()`).
2. **Fuzzy/keyword match**: the raw description has trailing city/country
   noise stripped (`strip_noise` — known suffixes like `DUBAI ARE`,
   `ABU DHABI ARE`, `AL AIN ARE`, etc., falling back to a generic trailing
   `ARE` strip), then checked against every mapping row's *keyword*
   (`keyword_for(cleaned_merchant.name)`: the cleaned name with
   parentheticals and non-alphanumeric characters removed, e.g.
   `"Nando's"` → `"NANDOS"`, `"e& (Etisalat) Telecom"` →
   `"E ETISALAT TELECOM"` — note this keyword derivation is not perfect
   for every name and may need per-row tuning). Keywords are tried
   **longest-first** so a more specific keyword (e.g. `"NOON MINUTES"`)
   wins over a shorter one that's also a substring of the description
   (e.g. `"NOON"`).
3. **No match** → the transaction is auto-cleaned (`auto_clean`: strip
   noise, title-case) and categorized `Uncategorized / Other` for
   *display only* in that run's spreadsheet — this is never written to
   `merchant_mapping`. The original description is also upserted into
   `unrecognized_merchants` (see [unrecognized_merchants.md](unrecognized_merchants.md)).

The whole `merchant_mapping` table (joined to `cleaned_merchants`) is
loaded into memory once per `MatchingEngine()` instantiation (once per
`/upload` request) rather than queried per row — fine at this table size
(tens to low hundreds of rows).

## Mapping Manager (`/admin/mappings`, admin-only)

- **Search**: filters on `original_description`, `cleaned_name`, or
  `category` (case-insensitive substring, `ilike`).
- **Add row** (`POST /admin/mappings/add`): rejects duplicates
  (case-insensitive on `original_description`); on success, resolves any
  matching `unrecognized_merchants` row (Section 0c behavior — a manually
  added mapping should not still show as outstanding queue work).
- **Edit row** (`POST /admin/mappings/<id>/edit`): resolves the typed
  `cleaned_name`/`category` via `CleanedMerchant.find_or_create()` and
  repoints the row's `cleaned_merchant_id` at it — `original_description`
  is not editable in place (delete + re-add if it needs to change, since
  it's the unique match key). Typing a name that already exists on
  another row merges this row onto that same canonical merchant; typing
  a different category for an existing name updates that merchant's
  category for every row that shares it.
- **Delete row**.
- **Bulk-assign category** (`POST /admin/mappings/bulk-category`):
  multi-select via checkboxes, resolves the distinct `cleaned_merchant`
  rows behind the selection and updates `cleaned_merchants.category` for
  those.
- **Rename category everywhere** (`POST /admin/mappings/rename-category`):
  `UPDATE cleaned_merchants SET category = ? WHERE category = ?` — the
  one-click category-restructuring action described in the spec.
- **Export/Import CSV**: same `original_description, cleaned_name,
  category` shape as the seed file and as the Unrecognized Merchants
  import (Section 4b) — upserts by `original_description`
  (case-insensitive), and also resolves matching `unrecognized_merchants`
  rows on import.
- Every route above is wrapped in `admin_required` (`app/admin.py`),
  which checks `current_user.is_admin` server-side and returns 403 —
  independent of anything hidden in the nav.

## Category list

`category` is a free-text column on `cleaned_merchants`; there is no
separate categories table or enum. The dropdown/datalist shown in the
Mapping Manager is derived at render time from
`SELECT DISTINCT category FROM cleaned_merchants` (`app/admin.py:mappings`).
See `FEATURES.md` for the current starting category list and the
Shopping-vs-Retail split rule.

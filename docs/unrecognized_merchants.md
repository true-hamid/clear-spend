# Unrecognized Merchants queue

**Implementation**: `app/models.py:UnrecognizedMerchant`,
`app/matching.py:record_unrecognized`, `app/admin.py` (queue routes),
`app/templates/admin/unrecognized.html`.

## Purpose

Every transaction description that falls through both matching steps in
[mapping.md](mapping.md) (no exact match, no keyword match) is upserted
into `unrecognized_merchants` — this is the queue of merchants that still
need a human (or AI-assisted, offline) decision, independent of the
`Uncategorized / Other` label shown in that run's spreadsheet.

This happens for **every user's** uploads, not just admins' — a normal
user's `/upload` populates the queue in the background even though they
have no UI to see it (Section 4c).

## Insert/update logic (`record_unrecognized`, called from `app/main.py:upload`)

For each unmatched transaction's `original_description`:
- If a row already exists (case-sensitive match on `original_description`
  — descriptions are raw statement text, not normalized, so two
  differently-cased variants of the "same" merchant currently create two
  rows; matching against `merchant_mapping` is case-insensitive but this
  queue key is not):
  - `occurrence_count += 1`
  - `total_amount += <this transaction's amount>`
  - `last_seen_at` bumped to now
  - if `status == 'exported'`, it's flipped back to `'pending'` — seen
    again after being exported but before being resolved, so it should
    still read as outstanding work.
- Otherwise, insert a new row with `occurrence_count = 1`,
  `total_amount = <amount>`, `status = 'pending'`.

Rows are **never auto-deleted** on export — only when resolved (mapping
added) or explicitly dismissed by an admin, so nothing already exported
is silently lost if the round trip to an AI agent never comes back.

## Admin screen (`/admin/unrecognized`)

Lists rows with `status` in `('pending', 'exported')`, sortable by
`total_amount` (default) or `occurrence_count`, both descending — surfaces
the merchants most worth cleaning up first.

- **Export pending** (`GET /admin/unrecognized/export`): downloads all
  `status='pending'` rows as
  `original_description, occurrence_count, total_amount, first_seen_at`
  and flips each exported row to `status='exported'`. This file is meant
  to be handed to an AI agent/session in a *separate* conversation — the
  running app itself makes no AI calls (see build_instructions.md
  Section 8).
- **Import resolved** (`POST /admin/unrecognized/import`): accepts a CSV
  shaped `original_description, cleaned_name, category` (whatever the AI
  agent or admin produced) and for each row: upserts into
  `merchant_mapping` (case-insensitive on `original_description`) and
  marks the matching `unrecognized_merchants` row `status='resolved'`.
- **Dismiss** (`POST /admin/unrecognized/<id>/delete`): removes a row
  from the queue *without* adding a mapping — for descriptions that
  aren't worth mapping (one-off noise, garbled OCR, etc.).
- Restricted to `admin` role via the same `admin_required` decorator used
  throughout `app/admin.py`.

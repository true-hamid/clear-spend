# ClearSpend — Feature Specification (source of truth)

This describes **current, actual behavior**. When behavior changes, update
the relevant file here first (or as part of the same change) — if the code
and these docs ever disagree, that's a bug in one of the two, not something
to improvise around.

- [docs/auth.md](docs/auth.md) — accounts, roles, sessions
- [docs/parsing.md](docs/parsing.md) — PDF ingestion
- [docs/mapping.md](docs/mapping.md) — merchant matching engine + Mapping Manager
- [docs/unrecognized_merchants.md](docs/unrecognized_merchants.md) — the review queue
- [docs/output.md](docs/output.md) — the generated Excel workbook

## Category list

Dining & Food Delivery, Groceries, Healthcare & Pharmacy, Home Services &
Laundry, Kids & Education, Transport & Fuel, Utilities & Telecom,
Government & Digital Services, Shopping, Retail, Entertainment,
Subscriptions, Payments & Transfers, Installments & EMI, Uncategorized /
Other.

`category` is free text (no enum/table) — the Mapping Manager derives its
dropdown from `SELECT DISTINCT category FROM merchant_mapping` at render
time (`app/admin.py:mappings`), so adding a new category is just adding a
mapping row with that category string; no schema change needed.

**Shopping vs. Retail** is a deliberate split: "Shopping" = the user's
called-out preferred/frequent spots, "Retail" = everything else retail-like.
Keep any future category splits explicit in the mapping table content, not
in code.

**Installments & EMI** is different from the others: it's never assigned
via `merchant_mapping` at all. Some statements (Mashreq's "Deal Summary"
ledger) list active installment/EMI plans with **no merchant name**
whatsoever — nothing for `MatchingEngine` to match against. `parse_pdf`
(`app/pdf_parser.py`) sets `RawTransaction.category_override` directly for
these rows, and `app/main.py:upload` assigns that category straight
through, skipping `MatchingEngine` and the Needs Review queue entirely —
see `docs/parsing.md`.

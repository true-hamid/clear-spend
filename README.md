# ClearSpend

A small, self-hosted, rule-based credit card statement categorizer. No LLM
calls at runtime — PDF parsing, merchant matching, and Excel generation are
all plain deterministic code. See `build_instructions.md` for the original
spec and `FEATURES.md` / `docs/` for current, as-built behavior.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python manage.py init-db
python manage.py import-mapping merchant_mapping.csv
python manage.py create-user admin --role admin
```

`create-user` prompts for a password (min. 8 characters) and hashes it —
nothing is ever stored in plaintext. Create additional accounts the same
way (`--role user` for normal users). There is **no signup page and no
in-app password reset** — both are deliberately CLI-only:

```powershell
python manage.py create-user alice --role user
python manage.py reset-password alice
```

## Running

```powershell
$env:FLASK_APP = "app:create_app"
flask run
```

Or with the dev server directly:

```powershell
python -c "from app import create_app; create_app().run(debug=True)"
```

Visit `http://127.0.0.1:5000`, log in, and upload PDF statements.

## Formula recalculation (optional but recommended)

openpyxl writes live `SUMIF`/`COUNTIF` formulas but never computes their
results. After building each workbook, the app tries to run it through
LibreOffice headless (`soffice --headless --convert-to xlsx`) so the
Category Summary tab shows pre-computed numbers instead of `0` until Excel
recalculates on open. If `soffice` isn't on `PATH`, the app skips this step
(logging a warning server-side) — the formulas are still correct and will
compute normally the first time the file is opened in Excel. Install
[LibreOffice](https://www.libreoffice.org/) on the host if you want the
pre-computed values.

## Deployment notes

- Set `SECRET_KEY` and `DATABASE_URL` env vars in production; the defaults
  are dev-only.
- Serve over HTTPS — `SESSION_COOKIE_SECURE` defaults to on (set
  `SESSION_COOKIE_SECURE=0` only for local HTTP development).
- This app is closed/authenticated-only: every route requires login, and
  `/admin/*` routes additionally require the `admin` role, checked
  server-side (`app/admin.py`'s `admin_required` decorator) — not just
  hidden in the nav.
- Uploaded PDFs and parsed transactions are never written to the database;
  only `users`, `merchant_mapping`, and `unrecognized_merchants` persist.
  Generated workbooks live in a short-lived temp file, referenced by a
  random token, and are deleted after download or after 30 minutes.

## Running tests

```powershell
pip install -r requirements-dev.txt
pytest
```

78 tests cover the matching engine, PDF parsing (including
`tests/fixtures/sample_statement.pdf`, a redacted real statement export —
see `docs/parsing.md`), the Excel builder, auth/role enforcement, the
Mapping Manager and Unrecognized Merchants admin routes, and the full
upload → download flow via Flask's test client. `requirements-dev.txt`
pulls in `pytest` and `fpdf2` (used only to generate throwaway synthetic
statement PDFs for parser edge-case tests) on top of the runtime
dependencies.

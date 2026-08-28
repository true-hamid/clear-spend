# Auth

**Implementation**: `app/models.py:User`, `app/auth.py`, `manage.py`.

- Two roles: `admin`, `user`, stored on `users.role`.
- Passwords are salted/hashed with `werkzeug.security.generate_password_hash`
  (`app/models.py` / `manage.py`) — plaintext is never stored or logged.
- Sessions are Flask-Login's standard server-signed cookie session
  (`app/__init__.py`: `login_manager`), cookie flags `HttpOnly`,
  `SameSite=Lax`, `Secure` (on by default; disable only for local HTTP dev
  via `SESSION_COOKIE_SECURE=0`).
- **No signup route exists.** The only way to create a user or reset a
  password is `manage.py create-user` / `manage.py reset-password`, run
  directly against the database. This is intentional per the closed,
  ≤5-user, non-commercial scope — do not add a signup or self-service
  password-reset flow.
- Every route except `GET/POST /login` requires `@login_required`
  (`flask_login`). Routes under `/admin/*` additionally require
  `current_user.is_admin`, enforced server-side by the `admin_required`
  decorator in `app/admin.py` (returns HTTP 403, not just a hidden nav
  link) — this is checked on every request, not cached client-side.

# Auth

**Implementation**: `app/models.py:User`, `app/auth.py`, `app/admin.py`, `manage.py`.

- Two roles: `admin`, `user`, stored on `users.role`.
- Passwords are salted/hashed with `werkzeug.security.generate_password_hash`
  (`app/models.py` / `app/auth.py` / `app/admin.py` / `manage.py`) —
  plaintext is never stored or logged.
- Sessions are Flask-Login's standard server-signed cookie session
  (`app/__init__.py`: `login_manager`), cookie flags `HttpOnly`,
  `SameSite=Lax`, `Secure` (on by default; disable only for local HTTP dev
  via `SESSION_COOKIE_SECURE=0`).
- **No self-service signup exists**, and creating an **admin** account is
  still CLI-only (`manage.py create-user --role admin`), run directly
  against the database. This remains intentional per the closed,
  non-commercial scope — do not add self-service signup or an in-app way
  for a non-admin to create an admin account.
- **Admins can create normal ('user'-role) accounts from the UI**:
  `GET/POST /admin/users` (`admin.users` / `admin.users_create` in
  `app/admin.py`, `admin_required`). The admin sets the new user's initial
  password directly in the form (min. 8 characters, with confirmation) —
  there's no email delivery step. The form only ever creates `role="user"`
  accounts, even if a request tries to smuggle a different `role` field;
  creating additional admins stays CLI-only.
- **Any logged-in user (admin or user) can change their own password** via
  `GET/POST /account/password` (`auth.change_password` in `app/auth.py`,
  `@login_required`). Requires the current password, a new password (min.
  8 characters), and matching confirmation. This is a logged-in
  self-service change, not an unauthenticated "forgot password" reset —
  `manage.py reset-password` remains the only way to reset a password for
  an account the admin can't otherwise log into.
- Every route except `GET/POST /login` requires `@login_required`
  (`flask_login`). Routes under `/admin/*` additionally require
  `current_user.is_admin`, enforced server-side by the `admin_required`
  decorator in `app/admin.py` (returns HTTP 403, not just a hidden nav
  link) — this is checked on every request, not cached client-side.

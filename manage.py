"""Admin CLI: the only way to create accounts, reset passwords, and seed the
merchant mapping table. No self-service signup exists in the app itself.

Usage:
  python manage.py init-db
  python manage.py create-user <username> --role admin|user
  python manage.py reset-password <username>
  python manage.py import-mapping <path-to-csv>
"""
import argparse
import csv
import getpass
import sys

from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import CleanedMerchant, MerchantMapping, User


def cmd_init_db(args):
    app = create_app()
    with app.app_context():
        db.create_all()
    print("Database initialized.")


def cmd_create_user(args):
    if args.role not in ("admin", "user"):
        print("Error: --role must be 'admin' or 'user'.", file=sys.stderr)
        sys.exit(1)

    app = create_app()
    with app.app_context():
        if User.query.filter_by(username=args.username).first():
            print(f"Error: user '{args.username}' already exists.", file=sys.stderr)
            sys.exit(1)

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)
        if len(password) < 8:
            print("Error: password must be at least 8 characters.", file=sys.stderr)
            sys.exit(1)

        user = User(
            username=args.username,
            password_hash=generate_password_hash(password),
            role=args.role,
        )
        db.session.add(user)
        db.session.commit()
        print(f"Created {args.role} user '{args.username}'.")


def cmd_reset_password(args):
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username=args.username).first()
        if not user:
            print(f"Error: user '{args.username}' not found.", file=sys.stderr)
            sys.exit(1)

        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm new password: ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)
        if len(password) < 8:
            print("Error: password must be at least 8 characters.", file=sys.stderr)
            sys.exit(1)

        user.password_hash = generate_password_hash(password)
        db.session.commit()
        print(f"Password reset for '{args.username}'.")


def cmd_import_mapping(args):
    app = create_app()
    with app.app_context():
        with open(args.csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            upserted = 0
            for row in reader:
                original_description = (row.get("original_description") or "").strip()
                cleaned_name = (row.get("cleaned_name") or "").strip()
                category = (row.get("category") or "").strip()
                if not (original_description and cleaned_name and category):
                    continue
                merchant = CleanedMerchant.find_or_create(cleaned_name, category)
                existing = MerchantMapping.query.filter(
                    db.func.lower(MerchantMapping.original_description) == original_description.lower()
                ).first()
                if existing:
                    existing.cleaned_merchant = merchant
                else:
                    db.session.add(
                        MerchantMapping(
                            original_description=original_description,
                            cleaned_merchant=merchant,
                        )
                    )
                upserted += 1
            db.session.commit()
        print(f"Imported/updated {upserted} mapping row(s) from {args.csv_path}.")


def main():
    parser = argparse.ArgumentParser(description="ClearSpend admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    p_create = sub.add_parser("create-user")
    p_create.add_argument("username")
    p_create.add_argument("--role", required=True, choices=["admin", "user"])
    p_create.set_defaults(func=cmd_create_user)

    p_reset = sub.add_parser("reset-password")
    p_reset.add_argument("username")
    p_reset.set_defaults(func=cmd_reset_password)

    p_import = sub.add_parser("import-mapping")
    p_import.add_argument("csv_path")
    p_import.set_defaults(func=cmd_import_mapping)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

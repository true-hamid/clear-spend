import csv
import os
import tempfile
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app import db as _db
from app.models import CleanedMerchant, MerchantMapping, User

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_STATEMENT_PDF = FIXTURES_DIR / "sample_statement.pdf"
REPO_ROOT = Path(__file__).parent.parent
MERCHANT_MAPPING_CSV = REPO_ROOT / "merchant_mapping.csv"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "adminpass123"
USER_USERNAME = "alice"
USER_PASSWORD = "alicepass123"


@pytest.fixture
def app():
    """A fresh Flask app with its own temp SQLite file per test — avoids
    Flask-SQLAlchemy's connection-pooling quirks with sqlite:///:memory:
    (multiple connections would otherwise see separate empty databases)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            # The test client talks plain HTTP; a Secure-flagged cookie
            # would otherwise be silently dropped between requests.
            "SESSION_COOKIE_SECURE": False,
            "SECRET_KEY": "test-secret-key",
        }
    )
    with application.app_context():
        _db.create_all()

    yield application

    try:
        os.remove(db_path)
    except OSError:
        pass


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_user(app):
    with app.app_context():
        user = User(
            username=ADMIN_USERNAME,
            password_hash=generate_password_hash(ADMIN_PASSWORD),
            role="admin",
        )
        _db.session.add(user)
        _db.session.commit()
        return user.id


@pytest.fixture
def normal_user(app):
    with app.app_context():
        user = User(
            username=USER_USERNAME,
            password_hash=generate_password_hash(USER_PASSWORD),
            role="user",
        )
        _db.session.add(user)
        _db.session.commit()
        return user.id


@pytest.fixture
def seeded_mapping(app):
    """Imports the real merchant_mapping.csv seed data used in production."""
    with app.app_context():
        with open(MERCHANT_MAPPING_CSV, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                merchant = CleanedMerchant.find_or_create(
                    row["cleaned_name"].strip(), row["category"].strip()
                )
                _db.session.add(
                    MerchantMapping(
                        original_description=row["original_description"].strip(),
                        cleaned_merchant=merchant,
                    )
                )
                count += 1
            _db.session.commit()
        return count


def login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def login_admin(client):
    return login(client, ADMIN_USERNAME, ADMIN_PASSWORD)


def login_user(client):
    return login(client, USER_USERNAME, USER_PASSWORD)

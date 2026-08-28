import argparse

import pytest
from werkzeug.security import check_password_hash

import manage
from app import create_app, db
from app.models import CleanedMerchant, MerchantMapping, User


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """manage.py's cmd_* functions each call create_app() with no override,
    so DATABASE_URL is the only lever to keep them off the real
    instance/clearspend.db during tests."""
    db_path = tmp_path / "manage_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    manage.cmd_init_db(argparse.Namespace())
    return db_path


def _query(db_path, fn):
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}"})
    with app.app_context():
        return fn()


class TestInitDb:
    def test_creates_the_database_file_and_tables(self, monkeypatch, tmp_path, capsys):
        db_path = tmp_path / "init_test.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        manage.cmd_init_db(argparse.Namespace())
        assert db_path.exists()
        assert "Database initialized." in capsys.readouterr().out


class TestCreateUser:
    def test_creates_user_with_hashed_password(self, isolated_db, monkeypatch, capsys):
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "supersecret1")
        manage.cmd_create_user(argparse.Namespace(username="alice", role="admin"))
        assert "Created admin user 'alice'." in capsys.readouterr().out
        user = _query(isolated_db, lambda: User.query.filter_by(username="alice").first())
        assert user is not None
        assert user.role == "admin"
        assert check_password_hash(user.password_hash, "supersecret1")

    def test_rejects_invalid_role(self, isolated_db):
        with pytest.raises(SystemExit):
            manage.cmd_create_user(argparse.Namespace(username="bob", role="superuser"))

    def test_rejects_duplicate_username(self, isolated_db, monkeypatch):
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "supersecret1")
        manage.cmd_create_user(argparse.Namespace(username="carol", role="user"))
        with pytest.raises(SystemExit):
            manage.cmd_create_user(argparse.Namespace(username="carol", role="user"))

    def test_rejects_mismatched_passwords(self, isolated_db, monkeypatch):
        responses = iter(["passwordone", "passwordtwo"])
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": next(responses))
        with pytest.raises(SystemExit):
            manage.cmd_create_user(argparse.Namespace(username="dave", role="user"))

    def test_rejects_short_password(self, isolated_db, monkeypatch):
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "short")
        with pytest.raises(SystemExit):
            manage.cmd_create_user(argparse.Namespace(username="erin", role="user"))


class TestResetPassword:
    def test_resets_password_for_existing_user(self, isolated_db, monkeypatch, capsys):
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "originalpass")
        manage.cmd_create_user(argparse.Namespace(username="frank", role="user"))

        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "newpassword1")
        manage.cmd_reset_password(argparse.Namespace(username="frank"))
        assert "Password reset for 'frank'." in capsys.readouterr().out

        user = _query(isolated_db, lambda: User.query.filter_by(username="frank").first())
        assert check_password_hash(user.password_hash, "newpassword1")

    def test_rejects_unknown_user(self, isolated_db):
        with pytest.raises(SystemExit):
            manage.cmd_reset_password(argparse.Namespace(username="ghost"))

    def test_rejects_mismatched_passwords(self, isolated_db, monkeypatch):
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "originalpass")
        manage.cmd_create_user(argparse.Namespace(username="gina", role="user"))

        responses = iter(["newone", "newtwo"])
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": next(responses))
        with pytest.raises(SystemExit):
            manage.cmd_reset_password(argparse.Namespace(username="gina"))

    def test_rejects_short_password(self, isolated_db, monkeypatch):
        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "originalpass")
        manage.cmd_create_user(argparse.Namespace(username="hank", role="user"))

        monkeypatch.setattr(manage.getpass, "getpass", lambda prompt="": "short")
        with pytest.raises(SystemExit):
            manage.cmd_reset_password(argparse.Namespace(username="hank"))


class TestImportMapping:
    def test_adds_new_mapping_rows(self, isolated_db, tmp_path, capsys):
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text("original_description,cleaned_name,category\nSHOP1,Shop One,Retail\n")
        manage.cmd_import_mapping(argparse.Namespace(csv_path=str(csv_path)))
        assert "Imported/updated 1 mapping row(s)" in capsys.readouterr().out

        def read():
            row = MerchantMapping.query.filter_by(original_description="SHOP1").first()
            return row and row.cleaned_merchant.name

        assert _query(isolated_db, read) == "Shop One"

    def test_updates_existing_mapping_row(self, isolated_db, tmp_path):
        def seed():
            merchant = CleanedMerchant.find_or_create("Old Name", "Retail")
            db.session.add(MerchantMapping(original_description="SHOP2", cleaned_merchant=merchant))
            db.session.commit()

        _query(isolated_db, seed)

        csv_path = tmp_path / "update.csv"
        csv_path.write_text("original_description,cleaned_name,category\nshop2,New Name,Shopping\n")
        manage.cmd_import_mapping(argparse.Namespace(csv_path=str(csv_path)))

        def read():
            row = MerchantMapping.query.filter_by(original_description="SHOP2").first()
            return row.cleaned_merchant.name, row.cleaned_merchant.category

        assert _query(isolated_db, read) == ("New Name", "Shopping")

    def test_skips_incomplete_rows(self, isolated_db, tmp_path):
        csv_path = tmp_path / "incomplete.csv"
        csv_path.write_text("original_description,cleaned_name,category\nSHOP3,,Retail\n")
        manage.cmd_import_mapping(argparse.Namespace(csv_path=str(csv_path)))

        row = _query(isolated_db, lambda: MerchantMapping.query.filter_by(original_description="SHOP3").first())
        assert row is None


class TestMainDispatch:
    def test_main_dispatches_to_init_db(self, monkeypatch, tmp_path):
        db_path = tmp_path / "main_test.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setattr("sys.argv", ["manage.py", "init-db"])
        manage.main()
        assert db_path.exists()

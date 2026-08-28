import io

from app.matching import record_unrecognized
from app.models import CleanedMerchant, MerchantMapping, UnrecognizedMerchant
from tests.conftest import login_admin


class TestMappingManagerCrud:
    def test_add_mapping_row(self, app, client, admin_user):
        login_admin(client)
        resp = client.post(
            "/admin/mappings/add",
            data={"original_description": "NEW SHOP DUBAI ARE", "cleaned_name": "New Shop", "category": "Retail"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            row = MerchantMapping.query.filter_by(original_description="NEW SHOP DUBAI ARE").first()
            assert row is not None
            assert row.cleaned_merchant.name == "New Shop"

    def test_add_duplicate_original_description_is_rejected(self, app, client, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("Dup", "Retail")
            db.session.add(MerchantMapping(original_description="DUP SHOP", cleaned_merchant=merchant))
            db.session.commit()

        login_admin(client)
        resp = client.post(
            "/admin/mappings/add",
            data={"original_description": "dup shop", "cleaned_name": "Dup Two", "category": "Retail"},
            follow_redirects=True,
        )
        assert b"already exists" in resp.data
        with app.app_context():
            assert MerchantMapping.query.filter_by(original_description="DUP SHOP").count() == 1

    def test_add_mapping_resolves_matching_unrecognized_row(self, app, client, admin_user):
        with app.app_context():
            record_unrecognized("PENDING SHOP", 10.0)
            from app import db

            db.session.commit()

        login_admin(client)
        client.post(
            "/admin/mappings/add",
            data={"original_description": "PENDING SHOP", "cleaned_name": "Pending Shop", "category": "Retail"},
        )
        with app.app_context():
            row = UnrecognizedMerchant.query.filter_by(original_description="PENDING SHOP").first()
            assert row.status == "resolved"

    def test_edit_mapping_updates_name_and_category(self, app, client, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("Old Name", "Retail")
            m = MerchantMapping(original_description="EDIT ME", cleaned_merchant=merchant)
            db.session.add(m)
            db.session.commit()
            mapping_id = m.id

        login_admin(client)
        client.post(
            f"/admin/mappings/{mapping_id}/edit",
            data={"cleaned_name": "New Name", "category": "Shopping"},
        )
        with app.app_context():
            from app import db

            row = db.session.get(MerchantMapping, mapping_id)
            assert row.cleaned_merchant.name == "New Name"
            assert row.cleaned_merchant.category == "Shopping"

    def test_delete_mapping_removes_row(self, app, client, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("X", "Retail")
            m = MerchantMapping(original_description="DELETE ME", cleaned_merchant=merchant)
            db.session.add(m)
            db.session.commit()
            mapping_id = m.id

        login_admin(client)
        client.post(f"/admin/mappings/{mapping_id}/delete")
        with app.app_context():
            from app import db

            assert db.session.get(MerchantMapping, mapping_id) is None

    def test_bulk_assign_category_updates_selected_rows(self, app, client, admin_user):
        with app.app_context():
            from app import db

            m1 = MerchantMapping(
                original_description="BULK1", cleaned_merchant=CleanedMerchant.find_or_create("B1", "Retail")
            )
            db.session.add(m1)
            m2 = MerchantMapping(
                original_description="BULK2", cleaned_merchant=CleanedMerchant.find_or_create("B2", "Retail")
            )
            db.session.add(m2)
            db.session.commit()
            ids = [m1.id, m2.id]

        login_admin(client)
        client.post(
            "/admin/mappings/bulk-category",
            data={"mapping_ids": [str(i) for i in ids], "new_category": "Shopping"},
        )
        with app.app_context():
            rows = MerchantMapping.query.filter(MerchantMapping.id.in_(ids)).all()
            assert all(r.cleaned_merchant.category == "Shopping" for r in rows)

    def test_rename_category_everywhere(self, app, client, admin_user):
        with app.app_context():
            from app import db

            db.session.add(MerchantMapping(original_description="R1", cleaned_merchant=CleanedMerchant.find_or_create("R1", "Old Cat")))
            db.session.add(MerchantMapping(original_description="R2", cleaned_merchant=CleanedMerchant.find_or_create("R2", "Old Cat")))
            db.session.add(MerchantMapping(original_description="R3", cleaned_merchant=CleanedMerchant.find_or_create("R3", "Other Cat")))
            db.session.commit()

        login_admin(client)
        client.post(
            "/admin/mappings/rename-category",
            data={"old_category": "Old Cat", "new_category_name": "New Cat"},
        )
        with app.app_context():
            assert CleanedMerchant.query.filter_by(category="Old Cat").count() == 0
            assert CleanedMerchant.query.filter_by(category="New Cat").count() == 2
            assert CleanedMerchant.query.filter_by(category="Other Cat").count() == 1

    def test_export_mapping_returns_csv(self, app, client, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("Exp One", "Retail")
            db.session.add(MerchantMapping(original_description="EXP1", cleaned_merchant=merchant))
            db.session.commit()

        login_admin(client)
        resp = client.get("/admin/mappings/export")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert b"EXP1" in resp.data
        assert b"Exp One" in resp.data

    def test_import_mapping_csv_upserts_rows(self, client, app, admin_user):
        login_admin(client)
        csv_content = "original_description,cleaned_name,category\nIMP1,Imported One,Retail\n"
        data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "import.csv")}
        resp = client.post("/admin/mappings/import", data=data, content_type="multipart/form-data")
        assert resp.status_code in (200, 302)
        with app.app_context():
            row = MerchantMapping.query.filter_by(original_description="IMP1").first()
            assert row is not None
            assert row.cleaned_merchant.name == "Imported One"

    def test_import_mapping_csv_updates_existing_row(self, client, app, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("Old Name", "Retail")
            db.session.add(MerchantMapping(original_description="IMP2", cleaned_merchant=merchant))
            db.session.commit()

        login_admin(client)
        csv_content = "original_description,cleaned_name,category\nimp2,New Name,Shopping\n"
        data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "import.csv")}
        client.post("/admin/mappings/import", data=data, content_type="multipart/form-data")
        with app.app_context():
            row = MerchantMapping.query.filter_by(original_description="IMP2").first()
            assert row.cleaned_merchant.name == "New Name"
            assert row.cleaned_merchant.category == "Shopping"

    def test_import_mapping_csv_skips_incomplete_rows(self, client, app, admin_user):
        login_admin(client)
        csv_content = "original_description,cleaned_name,category\nINCOMPLETE,,Retail\n"
        data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "import.csv")}
        client.post("/admin/mappings/import", data=data, content_type="multipart/form-data")
        with app.app_context():
            assert MerchantMapping.query.filter_by(original_description="INCOMPLETE").first() is None

    def test_add_mapping_requires_all_fields(self, client, admin_user):
        login_admin(client)
        resp = client.post(
            "/admin/mappings/add",
            data={"original_description": "", "cleaned_name": "", "category": ""},
            follow_redirects=True,
        )
        assert b"All fields are required" in resp.data

    def test_search_filters_by_original_description_cleaned_name_or_category(self, app, client, admin_user):
        with app.app_context():
            from app import db

            db.session.add(
                MerchantMapping(
                    original_description="SEARCHABLE SHOP",
                    cleaned_merchant=CleanedMerchant.find_or_create("Findable Merchant", "Groceries"),
                )
            )
            db.session.add(
                MerchantMapping(
                    original_description="OTHER SHOP",
                    cleaned_merchant=CleanedMerchant.find_or_create("Other Merchant", "Retail"),
                )
            )
            db.session.commit()

        login_admin(client)
        resp = client.get("/admin/mappings?q=Findable")
        assert b"SEARCHABLE SHOP" in resp.data
        assert b"OTHER SHOP" not in resp.data

    def test_bulk_assign_category_requires_selection_and_category(self, client, admin_user):
        login_admin(client)
        resp = client.post(
            "/admin/mappings/bulk-category",
            data={"mapping_ids": [], "new_category": ""},
            follow_redirects=True,
        )
        assert b"Select at least one row" in resp.data

    def test_rename_category_requires_both_names(self, client, admin_user):
        login_admin(client)
        resp = client.post(
            "/admin/mappings/rename-category",
            data={"old_category": "", "new_category_name": ""},
            follow_redirects=True,
        )
        assert b"Both the category to rename" in resp.data

    def test_import_mapping_requires_a_file(self, client, admin_user):
        login_admin(client)
        resp = client.post("/admin/mappings/import", data={}, content_type="multipart/form-data", follow_redirects=True)
        assert b"Choose a CSV file" in resp.data


class TestCleanedMerchantSharing:
    def test_two_original_descriptions_can_share_one_cleaned_merchant(self, app, client, admin_user):
        login_admin(client)
        client.post(
            "/admin/mappings/add",
            data={"original_description": "CARREFOUR BRANCH A", "cleaned_name": "Carrefour", "category": "Groceries"},
        )
        client.post(
            "/admin/mappings/add",
            data={"original_description": "CARREFOUR BRANCH B", "cleaned_name": "Carrefour", "category": "Groceries"},
        )
        with app.app_context():
            rows = MerchantMapping.query.filter(
                MerchantMapping.original_description.in_(["CARREFOUR BRANCH A", "CARREFOUR BRANCH B"])
            ).all()
            assert len(rows) == 2
            assert rows[0].cleaned_merchant_id == rows[1].cleaned_merchant_id
            assert CleanedMerchant.query.filter_by(name="Carrefour").count() == 1

    def test_editing_category_on_one_row_updates_every_row_sharing_the_merchant(self, app, client, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("Carrefour", "Groceries")
            m1 = MerchantMapping(original_description="CARREFOUR A", cleaned_merchant=merchant)
            m2 = MerchantMapping(original_description="CARREFOUR B", cleaned_merchant=merchant)
            db.session.add_all([m1, m2])
            db.session.commit()
            m1_id, m2_id = m1.id, m2.id

        login_admin(client)
        client.post(
            f"/admin/mappings/{m1_id}/edit",
            data={"cleaned_name": "Carrefour", "category": "Shopping"},
        )
        with app.app_context():
            from app import db

            assert db.session.get(MerchantMapping, m1_id).cleaned_merchant.category == "Shopping"
            assert db.session.get(MerchantMapping, m2_id).cleaned_merchant.category == "Shopping"


class TestUnrecognizedMerchantsQueue:
    def test_queue_lists_pending_and_exported_but_not_resolved(self, app, client, admin_user):
        with app.app_context():
            from app import db

            db.session.add_all(
                [
                    UnrecognizedMerchant(original_description="PENDING1", status="pending", total_amount=10),
                    UnrecognizedMerchant(original_description="EXPORTED1", status="exported", total_amount=20),
                    UnrecognizedMerchant(original_description="RESOLVED1", status="resolved", total_amount=30),
                ]
            )
            db.session.commit()

        login_admin(client)
        resp = client.get("/admin/unrecognized")
        assert b"PENDING1" in resp.data
        assert b"EXPORTED1" in resp.data
        assert b"RESOLVED1" not in resp.data

    def test_export_marks_pending_rows_as_exported(self, app, client, admin_user):
        with app.app_context():
            from app import db

            db.session.add(UnrecognizedMerchant(original_description="TOEXPORT", status="pending", total_amount=5))
            db.session.commit()

        login_admin(client)
        resp = client.get("/admin/unrecognized/export")
        assert resp.status_code == 200
        assert b"TOEXPORT" in resp.data
        with app.app_context():
            row = UnrecognizedMerchant.query.filter_by(original_description="TOEXPORT").first()
            assert row.status == "exported"

    def test_import_resolved_csv_creates_mapping_and_resolves_queue_row(self, app, client, admin_user):
        with app.app_context():
            from app import db

            db.session.add(UnrecognizedMerchant(original_description="TORESOLVE", status="pending", total_amount=5))
            db.session.commit()

        login_admin(client)
        csv_content = "original_description,cleaned_name,category\nTORESOLVE,Resolved Shop,Retail\n"
        data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "resolve.csv")}
        client.post("/admin/unrecognized/import", data=data, content_type="multipart/form-data")

        with app.app_context():
            mapping = MerchantMapping.query.filter_by(original_description="TORESOLVE").first()
            assert mapping is not None
            assert mapping.cleaned_merchant.name == "Resolved Shop"
            queue_row = UnrecognizedMerchant.query.filter_by(original_description="TORESOLVE").first()
            assert queue_row.status == "resolved"

    def test_import_resolved_csv_updates_existing_mapping(self, app, client, admin_user):
        with app.app_context():
            from app import db

            merchant = CleanedMerchant.find_or_create("Old Name", "Retail")
            db.session.add(MerchantMapping(original_description="ALREADYMAPPED", cleaned_merchant=merchant))
            db.session.commit()

        login_admin(client)
        csv_content = "original_description,cleaned_name,category\nALREADYMAPPED,New Name,Shopping\n"
        data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "resolve.csv")}
        client.post("/admin/unrecognized/import", data=data, content_type="multipart/form-data")

        with app.app_context():
            mapping = MerchantMapping.query.filter_by(original_description="ALREADYMAPPED").first()
            assert mapping.cleaned_merchant.name == "New Name"
            assert mapping.cleaned_merchant.category == "Shopping"

    def test_import_resolved_csv_skips_incomplete_rows(self, client, app, admin_user):
        login_admin(client)
        csv_content = "original_description,cleaned_name,category\nINCOMPLETE,,Retail\n"
        data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "resolve.csv")}
        client.post("/admin/unrecognized/import", data=data, content_type="multipart/form-data")
        with app.app_context():
            assert MerchantMapping.query.filter_by(original_description="INCOMPLETE").first() is None

    def test_unrecognized_import_requires_a_file(self, client, admin_user):
        login_admin(client)
        resp = client.post(
            "/admin/unrecognized/import", data={}, content_type="multipart/form-data", follow_redirects=True
        )
        assert b"Choose a CSV file" in resp.data

    def test_dismiss_deletes_row_without_creating_mapping(self, app, client, admin_user):
        with app.app_context():
            from app import db

            row = UnrecognizedMerchant(original_description="DISMISS_ME", status="pending", total_amount=5)
            db.session.add(row)
            db.session.commit()
            row_id = row.id

        login_admin(client)
        client.post(f"/admin/unrecognized/{row_id}/delete")
        with app.app_context():
            from app import db

            assert db.session.get(UnrecognizedMerchant, row_id) is None
            assert MerchantMapping.query.filter_by(original_description="DISMISS_ME").first() is None

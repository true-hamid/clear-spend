from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login, login_admin, login_user


class TestLogin:
    def test_login_page_loads_without_auth(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_successful_login_redirects_to_upload(self, client, admin_user):
        resp = login_admin(client)
        assert resp.status_code == 200
        assert b"Upload statement PDFs" in resp.data

    def test_wrong_password_shows_error_and_does_not_log_in(self, client, admin_user):
        resp = login(client, ADMIN_USERNAME, "wrong-password")
        assert resp.status_code == 200
        assert b"Invalid username or password" in resp.data

    def test_unknown_username_shows_generic_error(self, client):
        resp = login(client, "nobody", "whatever")
        assert b"Invalid username or password" in resp.data

    def test_logout_ends_session(self, client, admin_user):
        login_admin(client)
        client.get("/logout")
        resp = client.get("/upload")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestProtectedRoutes:
    def test_unauthenticated_upload_redirects_to_login_with_next(self, client):
        resp = client.get("/upload")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/login?next=%2Fupload"

    def test_authenticated_user_can_reach_upload(self, client, normal_user):
        login_user(client)
        resp = client.get("/upload")
        assert resp.status_code == 200

    def test_root_redirects_to_upload_when_authenticated(self, client, admin_user):
        login_admin(client)
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/upload"


class TestAdminRoleEnforcement:
    def test_normal_user_gets_403_on_mapping_manager(self, client, normal_user):
        login_user(client)
        resp = client.get("/admin/mappings")
        assert resp.status_code == 403

    def test_normal_user_gets_403_on_unrecognized_queue(self, client, normal_user):
        login_user(client)
        resp = client.get("/admin/unrecognized")
        assert resp.status_code == 403

    def test_normal_user_gets_403_on_admin_post_routes(self, client, normal_user):
        login_user(client)
        resp = client.post(
            "/admin/mappings/add",
            data={"original_description": "X", "cleaned_name": "Y", "category": "Retail"},
        )
        assert resp.status_code == 403

    def test_admin_can_reach_mapping_manager(self, client, admin_user):
        login_admin(client)
        resp = client.get("/admin/mappings")
        assert resp.status_code == 200

    def test_unauthenticated_admin_route_redirects_to_login_not_403(self, client):
        # Login is checked before role — an anonymous request should bounce
        # to /login, not leak a 403 (which would confirm the route exists
        # to an unauthenticated caller).
        resp = client.get("/admin/mappings")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_normal_user_nav_hides_admin_links(self, client, normal_user):
        resp = login_user(client)
        assert b"Mapping Manager" not in resp.data
        assert b"Unrecognized Merchants" not in resp.data

    def test_admin_nav_shows_admin_links(self, client, admin_user):
        resp = login_admin(client)
        assert b"Mapping Manager" in resp.data
        assert b"Unrecognized Merchants" in resp.data

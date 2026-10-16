import pytest
from fastapi.testclient import TestClient

from app import auth, config
from app.main import app

HDR = {"Origin": "https://other.example"}


@pytest.fixture()
def client():
    config.config_path("auth").unlink(missing_ok=True)
    auth.SETUP_PASSWORD_FILE.unlink(missing_ok=True)
    auth.lockout.success()
    with TestClient(app, follow_redirects=False) as c:
        yield c
    auth.lockout.success()


def _setup(c, password="supersecret1"):
    r = c.post("/setup", data={"username": "admin", "password": password, "confirm": password,
                               "setup_password": auth.setup_password()})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert not auth.SETUP_PASSWORD_FILE.exists()


def test_fresh_install_redirects_to_setup(client):
    assert client.get("/").headers["location"] == "/setup"
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_setup_validates_password(client):
    sp = auth.setup_password()
    assert auth.SETUP_PASSWORD_FILE.exists()
    r = client.post("/setup", data={"username": "admin", "password": "longenough1", "confirm": "longenough1",
                                    "setup_password": "wrong"})
    assert r.status_code == 403
    r = client.post("/setup", data={"username": "admin", "password": "short", "confirm": "short", "setup_password": sp})
    assert r.status_code == 400
    r = client.post("/setup", data={"username": "admin", "password": "longenough1", "confirm": "different1",
                                    "setup_password": sp})
    assert r.status_code == 400
    assert not auth.is_setup_complete()


def test_setup_login_and_wrong_password(client):
    _setup(client)
    stored = config.load("auth")
    assert stored["setup_completed"] and stored["username"] == "admin" and stored["password_hash"].startswith("$2")
    assert client.post("/login", data={"username": "admin", "password": "wrong-pass"}).status_code == 401
    r = client.post("/login", data={"username": "admin", "password": "supersecret1"})
    assert r.status_code == 303 and auth.COOKIE_NAME in r.cookies
    assert client.get("/api/dashboard").status_code == 200


def test_lockout_after_five_failures(client):
    _setup(client)
    codes = [client.post("/login", data={"username": "admin", "password": "bad-pass"}).status_code for _ in range(5)]
    assert codes[:4] == [401] * 4 and codes[4] == 429
    assert client.post("/login", data={"username": "admin", "password": "supersecret1"}).status_code == 429


def test_change_password_invalidates_other_sessions(client):
    _setup(client)
    client.post("/login", data={"username": "admin", "password": "supersecret1"})
    old_cookie = client.cookies.get(auth.COOKIE_NAME)
    r = client.post("/api/auth/change-password", headers=HDR,
                    json={"old_password": "supersecret1", "new_password": "newsecret22", "confirm": "newsecret22"})
    assert r.status_code == 200
    assert config.load("auth")["session_epoch"] == 2
    assert auth.session_user(old_cookie) is None
    assert client.get("/api/dashboard").status_code == 200


def test_cross_site_requests_are_allowed(client):
    _setup(client)
    client.post("/login", data={"username": "admin", "password": "supersecret1"})
    pre = client.options("/api/mounts/check-all", headers={**HDR, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "https://other.example"
    r = client.post("/api/mounts/check-all", headers=HDR)
    assert r.status_code == 200 and r.headers["access-control-allow-credentials"] == "true"

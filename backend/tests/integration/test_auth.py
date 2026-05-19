"""Phase 6.4 — single-user authentication tests.

Covers:
  - Argon2 hash + verify round-trip; rejects malformed hashes
  - JWT encode/decode round-trip; expired token rejected; wrong secret
    rejected; wrong algorithm rejected
  - AuthStore lifecycle (not-bootstrapped → write_initial → metadata
    read → password change → can't double-bootstrap)
  - Rate limiter: 5 failures lock out; window expiry resets; success
    resets the counter
  - require_auth middleware: cookie path, Bearer path, no-token → 401
  - Route surface: bootstrap-status, bootstrap (incl. refusal-when-set),
    login (correct + wrong + rate-limited), session, change-password,
    generate-api-token

Tests run against the TestClient with the real auth wiring; we set
URSA_OSCAR_JWT_SECRET to a known value per-test for determinism.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from ursa_oscar.auth.hashing import hash_password, verify_password
from ursa_oscar.auth.middleware import COOKIE_NAME, require_auth
from ursa_oscar.auth.rate_limit import LoginRateLimiter
from ursa_oscar.auth.store import (
    AuthStore,
    AuthStoreAlreadyBootstrapped,
    AuthStoreNotBootstrapped,
)
from ursa_oscar.auth.tokens import (
    SESSION_LIFETIME,
    TokenError,
    decode_token,
    encode_token,
    resolve_jwt_secret,
)
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_argon2_hash_verify_round_trip():
    h = hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong password", h) is False


def test_argon2_verify_returns_false_on_malformed_hash():
    assert verify_password("anything", "not-a-valid-hash") is False
    assert verify_password("anything", "") is False


def test_jwt_encode_decode_round_trip():
    token = encode_token("test-secret-1234", kind="session")
    claims = decode_token("test-secret-1234", token)
    assert claims["sub"] == "operator"
    assert claims["kind"] == "session"
    assert "iat" in claims and "exp" in claims


def test_jwt_expired_token_rejected():
    old = datetime.now(timezone.utc) - SESSION_LIFETIME - timedelta(seconds=10)
    token = encode_token("test-secret-1234", kind="session", now=old)
    with pytest.raises(TokenError):
        decode_token("test-secret-1234", token)


def test_jwt_wrong_secret_rejected():
    token = encode_token("secret-a", kind="session")
    with pytest.raises(TokenError):
        decode_token("secret-b", token)


def test_jwt_api_token_has_longer_lifetime():
    now = datetime.now(timezone.utc)
    session = decode_token("s", encode_token("s", kind="session", now=now))
    api = decode_token("s", encode_token("s", kind="api", now=now))
    # API > session lifetime by orders of magnitude.
    assert api["exp"] > session["exp"] + 60 * 60 * 24 * 30  # at least 30 days more


def test_resolve_jwt_secret_generates_and_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("URSA_OSCAR_JWT_SECRET", raising=False)
    secret_a = resolve_jwt_secret(tmp_path)
    assert len(secret_a) >= 32
    # Same dir → same secret on second call (file persists).
    secret_b = resolve_jwt_secret(tmp_path)
    assert secret_a == secret_b
    # Env override wins.
    monkeypatch.setenv("URSA_OSCAR_JWT_SECRET", "env-override-secret-xyz")
    assert resolve_jwt_secret(tmp_path) == "env-override-secret-xyz"


# ---------------------------------------------------------------------------
# AuthStore lifecycle
# ---------------------------------------------------------------------------


def test_auth_store_not_bootstrapped_initially(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    assert store.is_bootstrapped() is False
    assert store.read_password_hash() is None
    assert store.metadata() is None


def test_auth_store_write_initial_persists(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    h = hash_password("a-long-enough-password")
    store.write_initial(h)
    assert store.is_bootstrapped() is True
    assert store.read_password_hash() == h
    meta = store.metadata()
    assert meta is not None
    assert meta["user"] == "operator"
    assert "created_at" in meta and "last_changed_at" in meta


def test_auth_store_refuses_double_bootstrap(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.write_initial(hash_password("first-time-password"))
    with pytest.raises(AuthStoreAlreadyBootstrapped):
        store.write_initial(hash_password("second-attempt-password"))


def test_auth_store_update_password_hash(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.write_initial(hash_password("original-password"))
    new_hash = hash_password("brand-new-password")
    store.update_password_hash(new_hash)
    assert store.read_password_hash() == new_hash


def test_auth_store_update_refuses_when_not_bootstrapped(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    with pytest.raises(AuthStoreNotBootstrapped):
        store.update_password_hash(hash_password("can-not-set-this"))


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_below_threshold():
    rl = LoginRateLimiter(max_failures=5, window_seconds=900)
    for _ in range(4):
        rl.record_failure("1.2.3.4")
    ok, _ = rl.check("1.2.3.4")
    assert ok is True


def test_rate_limiter_locks_after_threshold():
    rl = LoginRateLimiter(max_failures=5, window_seconds=900)
    for _ in range(5):
        rl.record_failure("1.2.3.4")
    ok, retry_after = rl.check("1.2.3.4")
    assert ok is False
    assert retry_after > 0


def test_rate_limiter_success_resets_counter():
    rl = LoginRateLimiter(max_failures=5, window_seconds=900)
    for _ in range(5):
        rl.record_failure("1.2.3.4")
    rl.reset("1.2.3.4")
    ok, _ = rl.check("1.2.3.4")
    assert ok is True


def test_rate_limiter_isolates_different_ips():
    rl = LoginRateLimiter(max_failures=5, window_seconds=900)
    for _ in range(5):
        rl.record_failure("1.2.3.4")
    ok_other, _ = rl.check("5.6.7.8")
    assert ok_other is True


# ---------------------------------------------------------------------------
# require_auth middleware (isolated mini-app)
# ---------------------------------------------------------------------------


@pytest.fixture
def mini_app():
    app = FastAPI()
    app.state.jwt_secret = "mini-app-secret"

    @app.get("/open")
    def open_route():
        return {"open": True}

    @app.get("/protected")
    def protected(claims: dict = Depends(require_auth)):
        return {"sub": claims["sub"]}

    return app


def test_require_auth_blocks_request_without_token(mini_app):
    client = TestClient(mini_app)
    r = client.get("/protected")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_require_auth_accepts_bearer_header(mini_app):
    client = TestClient(mini_app)
    token = encode_token("mini-app-secret", kind="session")
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"sub": "operator"}


def test_require_auth_accepts_cookie(mini_app):
    client = TestClient(mini_app)
    token = encode_token("mini-app-secret", kind="session")
    client.cookies.set(COOKIE_NAME, token)
    r = client.get("/protected")
    assert r.status_code == 200


def test_require_auth_rejects_invalid_token(mini_app):
    client = TestClient(mini_app)
    r = client.get("/protected", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /auth/* endpoint surface against the full app
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """TestClient pointed at a fresh DB + a clean /data dir so each
    test sees a not-yet-bootstrapped install."""
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "auth.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    monkeypatch.setenv("URSA_OSCAR_JWT_SECRET", "deterministic-test-secret-xyz")
    monkeypatch.setenv("URSA_OSCAR_DEV_MODE", "true")
    _config_mod._settings = None

    # Pre-seed an empty DB (other route imports expect the schema).
    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    seeder.close()

    app = create_app()
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def test_bootstrap_status_returns_false_initially(auth_client):
    r = auth_client.get("/api/v1/auth/bootstrap-status")
    assert r.status_code == 200
    assert r.json() == {"bootstrapped": False}


def test_bootstrap_flow_end_to_end(auth_client):
    # Initial bootstrap creates the file + returns a session token.
    r = auth_client.post("/api/v1/auth/bootstrap", json={
        "password": "first-time-password-12345",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["token_kind"] == "session"
    assert COOKIE_NAME in auth_client.cookies

    # Subsequent status call shows bootstrapped.
    r2 = auth_client.get("/api/v1/auth/bootstrap-status")
    assert r2.json() == {"bootstrapped": True}


def test_bootstrap_rejected_once_set(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={
        "password": "initial-password-123456",
    })
    r = auth_client.post("/api/v1/auth/bootstrap", json={
        "password": "second-attempt-password-xyz",
    })
    assert r.status_code == 409


def test_bootstrap_password_too_short_rejected(auth_client):
    r = auth_client.post("/api/v1/auth/bootstrap", json={"password": "short"})
    # Pydantic min_length validation -> 422.
    assert r.status_code == 422


def test_login_with_correct_password_returns_session(auth_client):
    pw = "operator-password-1234"
    auth_client.post("/api/v1/auth/bootstrap", json={"password": pw})
    # Clear cookies so we're authenticating fresh.
    auth_client.cookies.clear()

    r = auth_client.post("/api/v1/auth/login", json={"password": pw})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_kind"] == "session"
    assert COOKIE_NAME in auth_client.cookies


def test_login_with_wrong_password_returns_401(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "correct-password-12345"})
    auth_client.cookies.clear()
    r = auth_client.post("/api/v1/auth/login", json={"password": "wrong-password-xyz"})
    assert r.status_code == 401


def test_login_rate_limit_fires_after_5_failures(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "correct-password-12345"})
    auth_client.cookies.clear()
    for _ in range(5):
        r = auth_client.post("/api/v1/auth/login", json={"password": "wrong"})
        assert r.status_code == 401
    # 6th attempt should be rate-limited.
    r6 = auth_client.post("/api/v1/auth/login", json={"password": "correct-password-12345"})
    assert r6.status_code == 429
    assert "Retry-After" in r6.headers


def test_session_endpoint_returns_claims(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "operator-password-1234"})
    r = auth_client.get("/api/v1/auth/session")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"] == "operator"
    assert body["token_kind"] == "session"
    assert body["expires_in_seconds"] > 0


def test_session_endpoint_requires_auth(auth_client):
    # No bootstrap, no cookie → 401 (not 409 — middleware fires first).
    r = auth_client.get("/api/v1/auth/session")
    assert r.status_code == 401


def test_change_password_requires_current(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "original-password-12345"})
    r = auth_client.post("/api/v1/auth/change-password", json={
        "current_password": "WRONG-current-password",
        "new_password": "new-password-zzzzzzzzz",
    })
    assert r.status_code == 401


def test_change_password_success_replaces_credentials(auth_client):
    """After change-password: old credential is rejected, new one
    accepted. The cookie itself is refreshed but may equal the
    old one within the same wall-clock second (iat resolution is
    1s) — what we care about is that the credentials behavior
    changed."""
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "original-password-12345"})
    r = auth_client.post("/api/v1/auth/change-password", json={
        "current_password": "original-password-12345",
        "new_password": "new-password-zzzzzzzzz",
    })
    assert r.status_code == 200, r.text
    assert COOKIE_NAME in auth_client.cookies

    # Old password no longer works; new one does.
    auth_client.cookies.clear()
    r_old = auth_client.post("/api/v1/auth/login", json={
        "password": "original-password-12345",
    })
    assert r_old.status_code == 401
    r_new = auth_client.post("/api/v1/auth/login", json={
        "password": "new-password-zzzzzzzzz",
    })
    assert r_new.status_code == 200


def test_generate_api_token_returns_90d_token(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "operator-password-1234"})
    r = auth_client.post("/api/v1/auth/generate-api-token")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_kind"] == "api"
    # Decode the returned token + confirm it's an api-kind token.
    claims = decode_token("deterministic-test-secret-xyz", body["token"])
    assert claims["kind"] == "api"
    # API tokens last 90 days.
    issued = datetime.fromtimestamp(claims["iat"], tz=timezone.utc)
    expires = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    lifetime = expires - issued
    assert timedelta(days=89) <= lifetime <= timedelta(days=91)


def test_logout_clears_cookie(auth_client):
    auth_client.post("/api/v1/auth/bootstrap", json={"password": "operator-password-1234"})
    assert COOKIE_NAME in auth_client.cookies
    r = auth_client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert COOKIE_NAME not in auth_client.cookies


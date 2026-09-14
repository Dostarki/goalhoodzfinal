"""Backend tests verifying /api/auth/connect and /api/me do NOT invoke external RPC
and complete quickly (fix for intermittent Cloudflare 520 timeouts).
"""
import os
import re
import time
import secrets
import pytest
import requests
from pathlib import Path

# Load REACT_APP_BACKEND_URL from frontend/.env
def _load_base_url():
    env_path = Path('/app/frontend/.env')
    for line in env_path.read_text().splitlines():
        if line.startswith('REACT_APP_BACKEND_URL='):
            return line.split('=', 1)[1].strip().rstrip('/')
    raise RuntimeError('REACT_APP_BACKEND_URL not found')

BASE_URL = _load_base_url()
API = f"{BASE_URL}/api"

# Latency threshold - should be well under RPC scan time
FAST_THRESHOLD_S = 3.0


def rand_addr():
    return '0x' + secrets.token_hex(20)


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- /api/auth/connect ----------

class TestAuthConnect:
    def test_connect_returns_200_fast_with_token_and_user(self, session):
        addr = rand_addr()
        t0 = time.time()
        r = session.post(f"{API}/auth/connect", json={"address": addr}, timeout=10)
        elapsed = time.time() - t0
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert "token" in data and isinstance(data["token"], str) and len(data["token"]) > 0
        assert "user" in data and isinstance(data["user"], dict)
        assert data["user"].get("address", "").lower() == addr.lower()
        assert elapsed < FAST_THRESHOLD_S, f"connect took {elapsed:.2f}s (>{FAST_THRESHOLD_S}s)"

    def test_connect_invalid_address_400(self, session):
        r = session.post(f"{API}/auth/connect", json={"address": "0xdeadbeef"}, timeout=10)
        assert r.status_code == 400

    def test_connect_10_new_addresses_no_hang_no_5xx(self, session):
        latencies = []
        for i in range(10):
            addr = rand_addr()
            t0 = time.time()
            r = session.post(f"{API}/auth/connect", json={"address": addr}, timeout=10)
            elapsed = time.time() - t0
            latencies.append(elapsed)
            assert r.status_code == 200, f"iter {i}: status={r.status_code} body={r.text[:200]}"
            assert r.text.strip(), f"iter {i}: empty response body"
            data = r.json()
            assert "token" in data and "user" in data
        max_l = max(latencies)
        avg_l = sum(latencies) / len(latencies)
        print(f"\n10x /auth/connect latencies: max={max_l:.3f}s avg={avg_l:.3f}s all={[f'{x:.2f}' for x in latencies]}")
        assert max_l < FAST_THRESHOLD_S, f"max latency {max_l:.2f}s exceeds {FAST_THRESHOLD_S}s"


# ---------- /api/me ----------

class TestMe:
    @pytest.fixture(scope="class")
    def auth(self, session):
        addr = rand_addr()
        r = session.post(f"{API}/auth/connect", json={"address": addr}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        return {"addr": addr, "token": d["token"], "user": d["user"]}

    def test_me_returns_200_fast(self, session, auth):
        t0 = time.time()
        r = session.get(f"{API}/me", headers={"Authorization": f"Bearer {auth['token']}"}, timeout=10)
        elapsed = time.time() - t0
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert data.get("address", "").lower() == auth["addr"].lower()
        assert elapsed < FAST_THRESHOLD_S, f"/me took {elapsed:.2f}s"

    def test_me_no_token_401(self, session):
        r = session.get(f"{API}/me", timeout=10)
        assert r.status_code in (401, 403)


# ---------- /api/leaderboard ----------

class TestLeaderboard:
    def test_leaderboard_200_valid_json(self, session):
        r = session.get(f"{API}/leaderboard", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, (list, dict))


# ---------- /api/nft/status (RPC-backed, slower is OK) ----------

class TestNftStatus:
    def test_nft_status_returns_valid_json(self, session):
        addr = rand_addr()
        # Longer timeout since this endpoint uses RPC
        r = session.get(f"{API}/nft/status", params={"address": addr}, timeout=60)
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert "balance" in data
        assert "has_nft" in data
        assert isinstance(data["has_nft"], bool)

    def test_nft_status_invalid_address_400(self, session):
        r = session.get(f"{API}/nft/status", params={"address": "notanaddr"}, timeout=10)
        assert r.status_code == 400


# ---------- /api/me/username ----------

class TestUsername:
    @pytest.fixture(scope="class")
    def user_a(self, session):
        addr = rand_addr()
        r = session.post(f"{API}/auth/connect", json={"address": addr}, timeout=10)
        return r.json()

    @pytest.fixture(scope="class")
    def user_b(self, session):
        addr = rand_addr()
        r = session.post(f"{API}/auth/connect", json={"address": addr}, timeout=10)
        return r.json()

    def test_set_valid_username_200(self, session, user_a):
        uname = f"TEST_{secrets.token_hex(3)}"[:16]
        r = session.put(
            f"{API}/me/username",
            json={"username": uname},
            headers={"Authorization": f"Bearer {user_a['token']}"},
            timeout=10,
        )
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        assert r.json().get("username") == uname
        # persistence check via /me
        r2 = session.get(f"{API}/me", headers={"Authorization": f"Bearer {user_a['token']}"}, timeout=10)
        assert r2.json().get("username") == uname
        user_a["_uname"] = uname

    def test_duplicate_username_409(self, session, user_a, user_b):
        # user_a already has a username set; user_b tries to reuse it
        uname = user_a.get("_uname")
        if not uname:
            # set it first
            uname = f"TEST_{secrets.token_hex(3)}"[:16]
            session.put(
                f"{API}/me/username",
                json={"username": uname},
                headers={"Authorization": f"Bearer {user_a['token']}"},
                timeout=10,
            )
            user_a["_uname"] = uname
        r = session.put(
            f"{API}/me/username",
            json={"username": uname},
            headers={"Authorization": f"Bearer {user_b['token']}"},
            timeout=10,
        )
        assert r.status_code == 409, f"expected 409 got {r.status_code} body={r.text[:200]}"

    @pytest.mark.parametrize("bad", ["ab", "with space", "toolongusername_xxxxx", "bad-char!"])
    def test_invalid_username_400(self, session, user_a, bad):
        r = session.put(
            f"{API}/me/username",
            json={"username": bad},
            headers={"Authorization": f"Bearer {user_a['token']}"},
            timeout=10,
        )
        assert r.status_code == 400, f"input={bad!r} got {r.status_code}"

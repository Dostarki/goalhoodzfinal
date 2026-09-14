"""Backend tests for Futbot Early Access + Admin routes."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL') or 'https://goal-tracker-1318.preview.emergentagent.com'
BASE_URL = BASE_URL.rstrip('/')
API = f"{BASE_URL}/api"
ADMIN_PASSWORD = 'futbot-admin-2026'


def rand_wallet():
    return '0x' + uuid.uuid4().hex + uuid.uuid4().hex[:8]  # 40 hex


def rand_x():
    return ('t' + uuid.uuid4().hex)[:15]


@pytest.fixture(scope='module')
def s():
    return requests.Session()


@pytest.fixture(scope='module')
def admin_token(s):
    r = s.post(f"{API}/admin/login", json={'password': ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()['token']


# --- config ---
def test_config(s):
    r = s.get(f"{API}/early/config")
    assert r.status_code == 200
    j = r.json()
    assert 'settings' in j and 'daily_tasks' in j and 'today' in j
    assert 'follow_url' in j['settings']


# --- register validation ---
def test_register_invalid_x(s):
    r = s.post(f"{API}/early/register", json={'x_username': 'has space', 'wallet': rand_wallet()})
    assert r.status_code == 400


def test_register_invalid_wallet(s):
    r = s.post(f"{API}/early/register", json={'x_username': rand_x(), 'wallet': '0xNOPE'})
    assert r.status_code == 400


def test_register_and_reregister_idempotent(s):
    x, w = rand_x(), rand_wallet()
    r = s.post(f"{API}/early/register", json={'x_username': '@' + x, 'wallet': w})
    assert r.status_code == 200, r.text
    j1 = r.json()
    assert j1['participant']['x_username'] == x
    assert isinstance(j1['participant']['ticket_no'], int)
    assert 'token' in j1
    # same wallet + same x -> same ticket
    r2 = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': w})
    assert r2.status_code == 200
    assert r2.json()['participant']['ticket_no'] == j1['participant']['ticket_no']


def test_register_wallet_conflict(s):
    x, w = rand_x(), rand_wallet()
    r = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': w})
    assert r.status_code == 200
    r2 = s.post(f"{API}/early/register", json={'x_username': rand_x(), 'wallet': w})
    assert r2.status_code == 409


def test_register_xname_conflict(s):
    x, w = rand_x(), rand_wallet()
    r = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': w})
    assert r.status_code == 200
    r2 = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': rand_wallet()})
    assert r2.status_code == 409


# --- tasks ---
@pytest.fixture(scope='module')
def participant(s):
    x, w = rand_x(), rand_wallet()
    r = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': w})
    assert r.status_code == 200
    return r.json()


def test_tasks_require_token(s):
    r = s.post(f"{API}/early/tasks/follow/complete")
    assert r.status_code == 401


def test_tasks_unknown(s, participant):
    h = {'Authorization': f"Bearer {participant['token']}"}
    r = s.post(f"{API}/early/tasks/bogus/complete", headers=h)
    assert r.status_code == 400


def test_tasks_all_three(s, participant):
    h = {'Authorization': f"Bearer {participant['token']}"}
    cfg = s.get(f"{API}/early/config").json()['settings']
    expected = cfg['follow_points'] + cfg['rt_points'] + cfg['quote_points']
    for t in ['follow', 'rt', 'quote']:
        r = s.post(f"{API}/early/tasks/{t}/complete", headers=h)
        assert r.status_code == 200, r.text
    p = s.post(f"{API}/early/tasks/follow/complete", headers=h).json()  # idempotent
    assert p['points'] == expected
    assert p['completed'] is True
    assert p['completed_at']


def test_admin_route_rejects_participant(s, participant):
    h = {'Authorization': f"Bearer {participant['token']}"}
    r = s.get(f"{API}/admin/me", headers=h)
    assert r.status_code == 403


# --- early list ---
def test_early_list_completed_only(s, participant):
    r = s.get(f"{API}/early/list")
    assert r.status_code == 200
    j = r.json()
    assert 'total' in j and 'rows' in j
    for row in j['rows']:
        assert 'wallet' not in row
        assert row['completed'] is True
    # sorted desc by points
    pts = [row['points'] for row in j['rows']]
    assert pts == sorted(pts, reverse=True)


# --- admin ---
def test_admin_login_wrong(s):
    r = s.post(f"{API}/admin/login", json={'password': 'nope-nope-nope'})
    assert r.status_code in (401, 429)


def test_admin_me(s, admin_token):
    r = s.get(f"{API}/admin/me", headers={'Authorization': f'Bearer {admin_token}'})
    assert r.status_code == 200
    assert r.json()['role'] == 'admin'


def test_admin_settings_roundtrip(s, admin_token):
    h = {'Authorization': f'Bearer {admin_token}'}
    orig = s.get(f"{API}/admin/settings", headers=h).json()
    new = {**{k: orig[k] for k in ['follow_url', 'follow_points', 'rt_url', 'rt_points',
                                    'quote_text', 'quote_url', 'quote_points']}}
    new['rt_url'] = 'https://x.com/test_rt_' + uuid.uuid4().hex[:6]
    r = s.put(f"{API}/admin/settings", headers=h, json=new)
    assert r.status_code == 200
    assert r.json()['rt_url'] == new['rt_url']
    # verify persisted via config
    cfg = s.get(f"{API}/early/config").json()
    assert cfg['settings']['rt_url'] == new['rt_url']


def test_admin_daily_crud_and_complete(s, admin_token, participant):
    h = {'Authorization': f'Bearer {admin_token}'}
    body = {'title': 'TEST_daily_' + uuid.uuid4().hex[:6], 'url': 'https://x.com/t', 'points': 25, 'active': True}
    r = s.post(f"{API}/admin/daily-tasks", headers=h, json=body)
    assert r.status_code == 200
    tid = r.json()['id']
    # appears in public config
    cfg = s.get(f"{API}/early/config").json()
    assert any(t['id'] == tid for t in cfg['daily_tasks'])
    # participant completes it
    ph = {'Authorization': f"Bearer {participant['token']}"}
    r = s.post(f"{API}/early/daily/{tid}/complete", headers=ph)
    assert r.status_code == 200
    r2 = s.post(f"{API}/early/daily/{tid}/complete", headers=ph)
    assert r2.status_code == 409
    # pause -> not in config
    r = s.put(f"{API}/admin/daily-tasks/{tid}", headers=h, json={**body, 'active': False})
    assert r.status_code == 200
    cfg = s.get(f"{API}/early/config").json()
    assert not any(t['id'] == tid for t in cfg['daily_tasks'])
    # inactive complete -> 404
    r = s.post(f"{API}/early/daily/{tid}/complete", headers=ph)
    assert r.status_code == 404
    # delete
    r = s.delete(f"{API}/admin/daily-tasks/{tid}", headers=h)
    assert r.status_code == 200
    r = s.delete(f"{API}/admin/daily-tasks/{tid}", headers=h)
    assert r.status_code == 404


def test_admin_participants(s, admin_token):
    r = s.get(f"{API}/admin/participants", headers={'Authorization': f'Bearer {admin_token}'})
    assert r.status_code == 200
    assert isinstance(r.json(), list)

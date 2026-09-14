"""Backend tests for COLLAB feature - iteration 6."""
import os
import re
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://goal-tracker-1318.preview.emergentagent.com').rstrip('/')
ADMIN_PASSWORD = 'admin123'

# Valid 0x wallet generator
def wallet(seed: int) -> str:
    return '0x' + f'{seed:040x}'


@pytest.fixture(scope='module')
def admin_token():
    r = requests.post(f'{BASE_URL}/api/admin/login', json={'password': ADMIN_PASSWORD})
    assert r.status_code == 200, f'admin login failed: {r.status_code} {r.text}'
    return r.json()['token']


@pytest.fixture(scope='module')
def admin_headers(admin_token):
    return {'Authorization': f'Bearer {admin_token}'}


@pytest.fixture(scope='module')
def created_collabs():
    """Track collabs created in this module for cleanup."""
    ids = []
    yield ids
    # cleanup
    try:
        tok = requests.post(f'{BASE_URL}/api/admin/login', json={'password': ADMIN_PASSWORD}).json()['token']
        h = {'Authorization': f'Bearer {tok}'}
        for cid in ids:
            requests.delete(f'{BASE_URL}/api/admin/collabs/{cid}', headers=h)
    except Exception as e:
        print('cleanup error:', e)


def _create_collab(admin_headers, created_collabs, **overrides):
    body = {
        'name': f'TEST_{uuid.uuid4().hex[:8]}',
        'owner_x': 'ownerx',
        'gtd_spots': 3,
        'fcfs_spots': 3,
        'tweet_url': '',
    }
    body.update(overrides)
    r = requests.post(f'{BASE_URL}/api/admin/collabs', json=body, headers=admin_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    created_collabs.append(data['collab']['id'])
    return data


# ---------- Admin CRUD ----------
class TestAdminCollabs:
    def test_admin_routes_require_auth(self):
        for m, u in [('get', '/api/admin/collabs'), ('post', '/api/admin/collabs')]:
            r = getattr(requests, m)(f'{BASE_URL}{u}', json={} if m == 'post' else None)
            assert r.status_code == 401, f'{m} {u} => {r.status_code}'

    def test_create_collab_returns_code_and_hint(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs)
        assert 'code' in data and data['code'].startswith('GH-')
        assert len(data['code']) == 3 + 8 + 24
        c = data['collab']
        assert 'code_hint' in c
        assert 'code_hash' not in c and 'code_lookup' not in c
        assert c['gtd_used'] == 0 and c['fcfs_used'] == 0 and c['allocated'] == 0

    def test_invalid_owner_x(self, admin_headers):
        r = requests.post(f'{BASE_URL}/api/admin/collabs',
                          json={'name': 'TEST_bad', 'owner_x': 'bad name!', 'gtd_spots': 1, 'fcfs_spots': 1},
                          headers=admin_headers)
        assert r.status_code == 400

    def test_invalid_tweet_url(self, admin_headers):
        r = requests.post(f'{BASE_URL}/api/admin/collabs',
                          json={'name': 'TEST_tweet', 'owner_x': 'jack', 'gtd_spots': 1, 'fcfs_spots': 1,
                                'tweet_url': 'https://google.com/foo'},
                          headers=admin_headers)
        assert r.status_code == 400

    def test_list_collabs(self, admin_headers, created_collabs):
        _create_collab(admin_headers, created_collabs)
        r = requests.get(f'{BASE_URL}/api/admin/collabs', headers=admin_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert any(c['id'] == created_collabs[-1] for c in r.json())

    def test_update_and_shrink_below_allocated(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs, gtd_spots=2, fcfs_spots=2)
        cid = data['collab']['id']
        code = data['code']
        # partner login and add 1 GTD wallet
        tok = requests.post(f'{BASE_URL}/api/collab/login', json={'code': code}).json()['token']
        ph = {'Authorization': f'Bearer {tok}'}
        r = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'GTD', 'wallet': wallet(1)}, headers=ph)
        assert r.status_code == 200, r.text
        # try to shrink gtd_spots to 0 -> 400
        r = requests.put(f'{BASE_URL}/api/admin/collabs/{cid}',
                         json={'name': 'TEST_shrink', 'owner_x': 'ownerx', 'gtd_spots': 0, 'fcfs_spots': 2},
                         headers=admin_headers)
        assert r.status_code == 400
        # valid update
        r = requests.put(f'{BASE_URL}/api/admin/collabs/{cid}',
                         json={'name': 'TEST_upd', 'owner_x': 'ownerx', 'gtd_spots': 5, 'fcfs_spots': 5},
                         headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['name'] == 'TEST_upd'
        assert r.json()['gtd_spots'] == 5

    def test_rotate_code_invalidates_old(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs)
        cid, old_code = data['collab']['id'], data['code']
        # get partner token with old code
        old_tok = requests.post(f'{BASE_URL}/api/collab/login', json={'code': old_code}).json()['token']
        # rotate
        r = requests.post(f'{BASE_URL}/api/admin/collabs/{cid}/rotate', headers=admin_headers)
        assert r.status_code == 200
        new_code = r.json()['code']
        assert new_code.startswith('GH-') and new_code != old_code
        # old code login should fail
        r = requests.post(f'{BASE_URL}/api/collab/login', json={'code': old_code})
        assert r.status_code == 401
        # old token should fail
        r = requests.get(f'{BASE_URL}/api/collab/me', headers={'Authorization': f'Bearer {old_tok}'})
        assert r.status_code == 401
        # new code works
        r = requests.post(f'{BASE_URL}/api/collab/login', json={'code': new_code})
        assert r.status_code == 200

    def test_delete_removes_wallets(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs)
        cid, code = data['collab']['id'], data['code']
        tok = requests.post(f'{BASE_URL}/api/collab/login', json={'code': code}).json()['token']
        requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': wallet(2)},
                      headers={'Authorization': f'Bearer {tok}'})
        # delete
        r = requests.delete(f'{BASE_URL}/api/admin/collabs/{cid}', headers=admin_headers)
        assert r.status_code == 200
        created_collabs.remove(cid)
        # wallets endpoint 404
        r = requests.get(f'{BASE_URL}/api/admin/collabs/{cid}/wallets', headers=admin_headers)
        assert r.status_code == 404

    def test_unknown_id_404(self, admin_headers):
        fake = str(uuid.uuid4())
        r = requests.put(f'{BASE_URL}/api/admin/collabs/{fake}',
                         json={'name': 'TEST_x', 'owner_x': 'jack', 'gtd_spots': 1, 'fcfs_spots': 1},
                         headers=admin_headers)
        assert r.status_code == 404
        r = requests.delete(f'{BASE_URL}/api/admin/collabs/{fake}', headers=admin_headers)
        assert r.status_code == 404
        r = requests.post(f'{BASE_URL}/api/admin/collabs/{fake}/rotate', headers=admin_headers)
        assert r.status_code == 404

    def test_admin_wallets_lists(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs)
        cid, code = data['collab']['id'], data['code']
        tok = requests.post(f'{BASE_URL}/api/collab/login', json={'code': code}).json()['token']
        requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': wallet(3)},
                      headers={'Authorization': f'Bearer {tok}'})
        r = requests.get(f'{BASE_URL}/api/admin/collabs/{cid}/wallets', headers=admin_headers)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1 and rows[0]['wallet'] == wallet(3)


# ---------- Partner ----------
class TestPartner:
    def test_login_case_and_dash_insensitive(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs)
        code = data['code']
        # mangle: lowercase, insert spaces and dashes
        mangled = code.lower().replace('-', ' - ')
        r = requests.post(f'{BASE_URL}/api/collab/login', json={'code': mangled})
        assert r.status_code == 200, r.text
        assert 'token' in r.json()

    def test_wrong_code_401(self):
        # use distinct IP to avoid lockout side effects
        r = requests.post(f'{BASE_URL}/api/collab/login', json={'code': 'GH-BADCODEBADCODEBADCODEBADCODEBADCO'},
                          headers={'X-Forwarded-For': '10.99.0.1'})
        assert r.status_code == 401
        assert 'Invalid' in r.json().get('detail', '')

    def test_brute_force_lockout(self):
        ip = '10.99.0.2'
        headers = {'X-Forwarded-For': ip}
        for _ in range(5):
            requests.post(f'{BASE_URL}/api/collab/login', json={'code': 'GH-NOPENOPENOPENOPENOPENOPENOPENOPENO'},
                          headers=headers)
        r = requests.post(f'{BASE_URL}/api/collab/login', json={'code': 'GH-NOPENOPENOPENOPENOPENOPENOPENOPENO'},
                         headers=headers)
        assert r.status_code == 429

    def test_wallet_limits_and_dup(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs, gtd_spots=1, fcfs_spots=1)
        code = data['code']
        tok = requests.post(f'{BASE_URL}/api/collab/login', json={'code': code}).json()['token']
        h = {'Authorization': f'Bearer {tok}'}
        # add GTD
        r = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'GTD', 'wallet': wallet(10)}, headers=h)
        assert r.status_code == 200
        # duplicate same wallet within collab 409
        r = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'fcfs', 'wallet': wallet(10)}, headers=h)
        assert r.status_code == 409
        # exceed limit
        r = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': wallet(11)}, headers=h)
        assert r.status_code == 409
        assert 'GTD' in r.json().get('detail', '')
        # invalid wallet
        r = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': 'not-a-wallet'}, headers=h)
        assert r.status_code == 400

    def test_same_wallet_in_different_collab(self, admin_headers, created_collabs):
        w = wallet(20)
        d1 = _create_collab(admin_headers, created_collabs)
        d2 = _create_collab(admin_headers, created_collabs)
        t1 = requests.post(f'{BASE_URL}/api/collab/login', json={'code': d1['code']}).json()['token']
        t2 = requests.post(f'{BASE_URL}/api/collab/login', json={'code': d2['code']}).json()['token']
        r1 = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': w},
                           headers={'Authorization': f'Bearer {t1}'})
        r2 = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': w},
                           headers={'Authorization': f'Bearer {t2}'})
        assert r1.status_code == 200 and r2.status_code == 200

    def test_remove_wallet_cross_collab_404(self, admin_headers, created_collabs):
        d1 = _create_collab(admin_headers, created_collabs)
        d2 = _create_collab(admin_headers, created_collabs)
        t1 = requests.post(f'{BASE_URL}/api/collab/login', json={'code': d1['code']}).json()['token']
        t2 = requests.post(f'{BASE_URL}/api/collab/login', json={'code': d2['code']}).json()['token']
        r = requests.post(f'{BASE_URL}/api/collab/wallets', json={'type': 'gtd', 'wallet': wallet(30)},
                          headers={'Authorization': f'Bearer {t1}'})
        wid = r.json()['id']
        r = requests.delete(f'{BASE_URL}/api/collab/wallets/{wid}',
                            headers={'Authorization': f'Bearer {t2}'})
        assert r.status_code == 404
        # own can delete
        r = requests.delete(f'{BASE_URL}/api/collab/wallets/{wid}',
                            headers={'Authorization': f'Bearer {t1}'})
        assert r.status_code == 200

    def test_tweet_set_and_clear(self, admin_headers, created_collabs):
        data = _create_collab(admin_headers, created_collabs)
        tok = requests.post(f'{BASE_URL}/api/collab/login', json={'code': data['code']}).json()['token']
        h = {'Authorization': f'Bearer {tok}'}
        r = requests.put(f'{BASE_URL}/api/collab/tweet', json={'tweet_url': 'https://x.com/jack/status/20'}, headers=h)
        assert r.status_code == 200 and r.json()['tweet_url'] == 'https://x.com/jack/status/20'
        r = requests.put(f'{BASE_URL}/api/collab/tweet', json={'tweet_url': 'not a url'}, headers=h)
        assert r.status_code == 400
        r = requests.put(f'{BASE_URL}/api/collab/tweet', json={'tweet_url': ''}, headers=h)
        assert r.status_code == 200 and r.json()['tweet_url'] == ''


# ---------- Public ledger ----------
class TestLedger:
    def test_ledger_no_auth_no_secrets(self):
        r = requests.get(f'{BASE_URL}/api/collab/ledger')
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        for c in rows:
            assert 'wallets' not in c
            assert 'code_hash' not in c
            assert 'code_hint' not in c
            assert 'code_lookup' not in c
            assert 'allocated' in c
            assert 'owner_x' in c and 'tweet_url' in c

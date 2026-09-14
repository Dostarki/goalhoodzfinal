"""Backend tests for referral bonuses, VIP nonce/check, and expanded settings."""
import os
import time
import uuid
import pytest
import requests
from eth_account import Account
from eth_account.messages import encode_defunct

BASE_URL = (os.environ.get('REACT_APP_BACKEND_URL') or 'https://goal-tracker-1318.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"
ADMIN_PASSWORD = 'futbot-admin-2026'


def rand_wallet():
    return '0x' + uuid.uuid4().hex + uuid.uuid4().hex[:8]


def rand_x():
    return ('t' + uuid.uuid4().hex)[:15]


@pytest.fixture(scope='module')
def s():
    return requests.Session()


@pytest.fixture(scope='module')
def admin_h(s):
    r = s.post(f"{API}/admin/login", json={'password': ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return {'Authorization': f"Bearer {r.json()['token']}"}


@pytest.fixture(scope='module')
def settings(s):
    return s.get(f"{API}/early/config").json()['settings']


def register(s, x=None, w=None, ref=None):
    x = x or rand_x()
    w = w or rand_wallet()
    r = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': w, 'ref': ref})
    assert r.status_code == 200, r.text
    return r.json(), x, w


def h_of(reg):
    return {'Authorization': f"Bearer {reg['token']}"}


def complete_all(s, reg):
    ph = h_of(reg)
    for t in ('follow', 'rt', 'quote'):
        r = s.post(f"{API}/early/tasks/{t}/complete", headers=ph)
        assert r.status_code == 200, r.text
    return s.get(f"{API}/early/me", headers=ph).json()


# ---------- Settings with new fields ----------
def test_settings_defaults_present(s, admin_h):
    r = s.get(f"{API}/admin/settings", headers=admin_h)
    assert r.status_code == 200
    d = r.json()
    for k in ('referrer_points', 'referred_points', 'bronze_min', 'silver_min', 'gold_min',
              'platinum_min', 'bronze_points', 'silver_points', 'gold_points', 'platinum_points'):
        assert k in d, f"missing {k}"


def test_settings_put_new_fields_roundtrip(s, admin_h):
    orig = s.get(f"{API}/admin/settings", headers=admin_h).json()
    keep = {k: orig[k] for k in ('follow_url', 'follow_points', 'rt_url', 'rt_points',
                                 'quote_text', 'quote_url', 'quote_points')}
    body = {**keep,
            'referrer_points': 60, 'referred_points': 30,
            'bronze_min': 100, 'silver_min': 1000, 'gold_min': 10000, 'platinum_min': 50000,
            'bronze_points': 25, 'silver_points': 75, 'gold_points': 200, 'platinum_points': 500}
    r = s.put(f"{API}/admin/settings", headers=admin_h, json=body)
    assert r.status_code == 200
    d = r.json()
    assert d['referrer_points'] == 60 and d['referred_points'] == 30
    assert d['gold_min'] == 10000 and d['gold_points'] == 200
    # restore defaults 50/25
    body['referrer_points'] = 50
    body['referred_points'] = 25
    r = s.put(f"{API}/admin/settings", headers=admin_h, json=body)
    assert r.status_code == 200
    assert r.json()['referrer_points'] == 50


def test_settings_put_without_new_fields_uses_defaults(s, admin_h):
    orig = s.get(f"{API}/admin/settings", headers=admin_h).json()
    body = {k: orig[k] for k in ('follow_url', 'follow_points', 'rt_url', 'rt_points',
                                 'quote_text', 'quote_url', 'quote_points')}
    r = s.put(f"{API}/admin/settings", headers=admin_h, json=body)
    assert r.status_code == 200
    d = r.json()
    assert d['referrer_points'] == 50
    assert d['referred_points'] == 25
    assert d['gold_min'] == 10000


# ---------- Early list projection ----------
def test_early_list_projection(s):
    r = s.get(f"{API}/early/list")
    assert r.status_code == 200
    for row in r.json()['rows']:
        assert 'tier' in row
        assert 'referrals' in row
        for k in ('wallet', 'vip_wallet', 'volume_usd', 'vip_chains'):
            assert k not in row, f"'{k}' should not be exposed on /early/list"


# ---------- Referral flow ----------
def test_referral_ref_self_ignored(s):
    x, w = rand_x(), rand_wallet()
    r = s.post(f"{API}/early/register", json={'x_username': x, 'wallet': w, 'ref': x})
    assert r.status_code == 200
    assert r.json()['participant']['referred_by'] is None


def test_referral_ref_unknown_ignored(s):
    reg, _, _ = register(s, ref='nonexistent_' + uuid.uuid4().hex[:6])
    assert reg['participant']['referred_by'] is None


def test_referral_flow_credits_both_sides(s, settings):
    # referrer
    ref_reg, ref_x, _ = register(s)
    # complete referrer first (points don't matter for the credit path — but let's ensure referrer exists)
    # actually referrer just needs to exist; we don't need to complete them.

    # get referrer initial state
    ref_me = s.get(f"{API}/early/me", headers=h_of(ref_reg)).json()
    ref_points_before = ref_me['points']
    ref_refs_before = ref_me['referrals']

    # referred user registers with ref=ref_x
    referred_reg, _, _ = register(s, ref='@' + ref_x)
    assert referred_reg['participant']['referred_by'] == ref_x

    # complete all 3 tasks
    me = complete_all(s, referred_reg)
    task_pts = settings['follow_points'] + settings['rt_points'] + settings['quote_points']
    assert me['points'] == task_pts + settings['referred_points']
    assert me['referral_points'] == settings['referred_points']
    assert me['completed'] is True

    # referrer credited
    ref_me2 = s.get(f"{API}/early/me", headers=h_of(ref_reg)).json()
    assert ref_me2['points'] == ref_points_before + settings['referrer_points']
    assert ref_me2['referrals'] == ref_refs_before + 1
    assert ref_me2['referral_points'] >= settings['referrer_points']


def test_referral_idempotent_on_re_complete(s, settings):
    ref_reg, ref_x, _ = register(s)
    ref_points_before = s.get(f"{API}/early/me", headers=h_of(ref_reg)).json()['points']

    referred_reg, _, _ = register(s, ref=ref_x)
    complete_all(s, referred_reg)
    # try to re-complete: idempotent
    for t in ('follow', 'rt', 'quote'):
        r = s.post(f"{API}/early/tasks/{t}/complete", headers=h_of(referred_reg))
        assert r.status_code == 200

    task_pts = settings['follow_points'] + settings['rt_points'] + settings['quote_points']
    me = s.get(f"{API}/early/me", headers=h_of(referred_reg)).json()
    assert me['points'] == task_pts + settings['referred_points']  # no double credit
    assert me['referral_points'] == settings['referred_points']

    ref_me2 = s.get(f"{API}/early/me", headers=h_of(ref_reg)).json()
    assert ref_me2['points'] == ref_points_before + settings['referrer_points']  # no double
    assert ref_me2['referrals'] == 1


# ---------- VIP nonce & check ----------
def test_vip_nonce_requires_token(s):
    r = s.get(f"{API}/early/vip/nonce")
    assert r.status_code == 401


def test_vip_nonce_participant_message(s):
    reg, _, _ = register(s)
    r = s.get(f"{API}/early/vip/nonce", headers=h_of(reg))
    assert r.status_code == 200
    msg = r.json()['message']
    assert 'Futbot League VIP ticket check' in msg
    assert 'Nonce:' in msg


def test_vip_check_requires_completed(s):
    reg, _, _ = register(s)  # not completed
    # get a nonce to have valid message
    n = s.get(f"{API}/early/vip/nonce", headers=h_of(reg)).json()
    acct = Account.create()
    sig = acct.sign_message(encode_defunct(text=n['message'])).signature.hex()
    r = s.post(f"{API}/early/vip/check", headers=h_of(reg),
               json={'address': acct.address, 'message': n['message'], 'signature': sig})
    assert r.status_code == 400


def test_vip_check_invalid_address(s):
    reg, _, _ = register(s)
    complete_all(s, reg)
    n = s.get(f"{API}/early/vip/nonce", headers=h_of(reg)).json()
    acct = Account.create()
    sig = acct.sign_message(encode_defunct(text=n['message'])).signature.hex()
    r = s.post(f"{API}/early/vip/check", headers=h_of(reg),
               json={'address': '0xNOTAVALIDADDR', 'message': n['message'], 'signature': sig})
    assert r.status_code == 400


def test_vip_check_wrong_message(s):
    reg, _, _ = register(s)
    complete_all(s, reg)
    acct = Account.create()
    bad = "not a real message"
    sig = acct.sign_message(encode_defunct(text=bad)).signature.hex()
    r = s.post(f"{API}/early/vip/check", headers=h_of(reg),
               json={'address': acct.address, 'message': bad, 'signature': sig})
    assert r.status_code == 401


def test_vip_check_signer_mismatch(s):
    reg, _, _ = register(s)
    complete_all(s, reg)
    n = s.get(f"{API}/early/vip/nonce", headers=h_of(reg)).json()
    signer = Account.create()
    other = Account.create()
    sig = signer.sign_message(encode_defunct(text=n['message'])).signature.hex()
    r = s.post(f"{API}/early/vip/check", headers=h_of(reg),
               json={'address': other.address, 'message': n['message'], 'signature': sig})
    assert r.status_code == 401


def test_vip_check_valid_zero_volume_then_cooldown(s):
    """This calls real Etherscan — takes ~8s. Only runs once."""
    reg, _, _ = register(s)
    complete_all(s, reg)
    n = s.get(f"{API}/early/vip/nonce", headers=h_of(reg)).json()
    acct = Account.create()
    sig = acct.sign_message(encode_defunct(text=n['message'])).signature.hex()
    r = s.post(f"{API}/early/vip/check", headers=h_of(reg),
               json={'address': acct.address, 'message': n['message'], 'signature': sig},
               timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['tier'] is None
    assert d['volume_usd'] == 0 or d['volume_usd'] == 0.0
    assert d['bonus_points'] == 0
    assert 'chains' in d and len(d['chains']) == 8

    # immediate second check -> 429
    n2 = s.get(f"{API}/early/vip/nonce", headers=h_of(reg)).json()
    sig2 = acct.sign_message(encode_defunct(text=n2['message'])).signature.hex()
    r2 = s.post(f"{API}/early/vip/check", headers=h_of(reg),
                json={'address': acct.address, 'message': n2['message'], 'signature': sig2})
    assert r2.status_code == 429

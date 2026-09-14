"""VIP volume check flow test after ETHERSCAN_API_KEY fix."""
import os
import time
import secrets
import requests
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://goal-tracker-1318.preview.emergentagent.com').rstrip('/')
CORE_TASKS = ('follow', 'rt', 'quote')
EXPECTED_CHAINS = {'Ethereum', 'Arbitrum', 'Polygon', 'Linea', 'Blast', 'Gnosis', 'Mantle', 'Unichain'}


@pytest.fixture(scope='module')
def wallet():
    acct = Account.create()
    return acct


@pytest.fixture(scope='module')
def session():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='module')
def registered(session, wallet):
    x = f'qa_vol_{secrets.token_hex(3)}'
    r = session.post(f'{BASE_URL}/api/early/register', json={'x_username': x, 'wallet': wallet.address})
    assert r.status_code == 200, r.text
    data = r.json()
    token = data['token']
    session.headers.update({'Authorization': f'Bearer {token}'})
    # complete all core tasks
    for t in CORE_TASKS:
        rr = session.post(f'{BASE_URL}/api/early/tasks/{t}/complete')
        assert rr.status_code == 200, rr.text
    me = session.get(f'{BASE_URL}/api/early/me').json()
    assert me['completed'] is True
    assert me['completed_at'] is not None
    return {'token': token, 'x': x, 'participant': me}


def _get_nonce(session):
    r = session.get(f'{BASE_URL}/api/early/vip/nonce')
    assert r.status_code == 200, r.text
    return r.json()['message']


def test_root_ok(session):
    r = requests.get(f'{BASE_URL}/api/')
    assert r.status_code == 200


def test_config_ok():
    r = requests.get(f'{BASE_URL}/api/early/config')
    assert r.status_code == 200
    data = r.json()
    assert 'settings' in data
    assert 'daily_tasks' in data


def test_vip_check_success(session, wallet, registered):
    message = _get_nonce(session)
    signed = Account.sign_message(encode_defunct(text=message), private_key=wallet.key)
    r = session.post(f'{BASE_URL}/api/early/vip/check', json={
        'address': wallet.address, 'message': message, 'signature': signed.signature.hex()
    }, timeout=60)
    assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
    data = r.json()
    assert 'volume_usd' in data
    assert isinstance(data['volume_usd'], (int, float))
    assert 'chains' in data
    assert len(data['chains']) == 8
    chain_names = {c['chain'] for c in data['chains']}
    assert chain_names == EXPECTED_CHAINS, f'Chains mismatch: {chain_names}'
    for c in data['chains']:
        # fresh wallet: expect no config errors; error should be None or a runtime one
        err = c.get('error')
        assert err != 'Volume check is not configured'
        # For a fresh wallet, error should typically be None
        if err is not None:
            print(f"Chain {c['chain']} returned non-null error: {err}")
    assert 'tier' in data
    assert 'bonus_points' in data
    assert 'participant' in data


def test_vip_check_cooldown_429(session, wallet, registered):
    # request a new nonce, then attempt again immediately -> should be 429 cooldown
    message = _get_nonce(session)
    signed = Account.sign_message(encode_defunct(text=message), private_key=wallet.key)
    r = session.post(f'{BASE_URL}/api/early/vip/check', json={
        'address': wallet.address, 'message': message, 'signature': signed.signature.hex()
    }, timeout=30)
    assert r.status_code == 429, f'Expected 429, got {r.status_code}: {r.text}'
    assert 'recently' in r.text.lower() or 'try again' in r.text.lower()


def test_vip_check_invalid_signature_401():
    """Fresh participant, bogus signature -> 401 (cooldown not yet triggered)."""
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    acct = Account.create()
    x = f'qa_bad_{secrets.token_hex(3)}'
    r = s.post(f'{BASE_URL}/api/early/register', json={'x_username': x, 'wallet': acct.address})
    assert r.status_code == 200
    s.headers.update({'Authorization': f"Bearer {r.json()['token']}"})
    for t in CORE_TASKS:
        s.post(f'{BASE_URL}/api/early/tasks/{t}/complete')
    nonce_r = s.get(f'{BASE_URL}/api/early/vip/nonce')
    message = nonce_r.json()['message']
    bogus_sig = '0x' + '11' * 65
    r = s.post(f'{BASE_URL}/api/early/vip/check', json={
        'address': acct.address, 'message': message, 'signature': bogus_sig
    }, timeout=30)
    assert r.status_code == 401, f'Expected 401, got {r.status_code}: {r.text}'

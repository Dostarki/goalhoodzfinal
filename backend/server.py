from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from eth_account import Account
from eth_account.messages import encode_defunct
from pydantic import BaseModel, Field
from typing import List, Optional
import os
import re
import uuid
import random
import secrets
import logging
import jwt
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

from nft_gate import require_nft, nft_balance, gate_info  # noqa: E402
from nft_traits import token_bonus, owned_tokens  # noqa: E402

JWT_SECRET = os.environ.get('JWT_SECRET', 'dev-secret-change-me')
CHAIN_ID = int(os.environ.get('CHAIN_ID', '4663'))
APP_NAME = 'GoalHoodz - Early'
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{3,16}$')

CHAR_IDS = ['arc', 'mohawk', 'cap', 'afro', 'halo', 'spiky', 'glasses', 'crown']
ROUNDS = [
    [[0, 5], [1, 4], [2, 3]],
    [[0, 4], [5, 3], [1, 2]],
    [[0, 3], [4, 2], [5, 1]],
    [[0, 2], [3, 1], [4, 5]],
    [[0, 1], [2, 5], [3, 4]],
]

ADJ = ['swift', 'neon', 'silent', 'lucky', 'iron', 'pixel', 'turbo', 'cosmic', 'rapid', 'shadow',
       'golden', 'frost', 'wild', 'sonic', 'retro', 'mystic', 'blaze', 'crypto', 'vivid', 'nova']
NOUN = ['falcon', 'striker', 'wolf', 'kicker', 'tiger', 'rocket', 'viper', 'panda', 'hawk', 'goat',
        'ranger', 'knight', 'pilot', 'ghost', 'bison', 'otter', 'comet', 'lynx', 'rhino', 'sparrow']
HUMAN = ['kaan', 'mert', 'leo', 'alex', 'sam', 'nico', 'max', 'eli', 'jay', 'ryu', 'dani', 'omar',
         'luca', 'noah', 'zed', 'ivo', 'rey', 'kai', 'ege', 'ali']


def now():
    return datetime.now(timezone.utc)


def random_username():
    style = random.random()
    if style < 0.4:
        base = f"{random.choice(ADJ)}_{random.choice(NOUN)}"
    elif style < 0.75:
        base = f"{random.choice(HUMAN)}{random.choice(['', '_', '.'])}{random.choice(NOUN)}"
    else:
        base = f"{random.choice(HUMAN)}{random.choice(ADJ)}"
    if random.random() < 0.7:
        base += str(random.randint(2, 99))
    return base[:16]


def random_goals():
    r = random.random()
    if r < 0.25:
        return 0
    if r < 0.55:
        return 1
    if r < 0.8:
        return 2
    if r < 0.92:
        return 3
    return 4


def build_message(domain: str, address: str, nonce: str, issued_at: str) -> str:
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n{address}\n\n"
        f"Sign in to {APP_NAME} on Robinhood Chain.\n\n"
        f"URI: https://{domain}\nVersion: 1\nChain ID: {CHAIN_ID}\nNonce: {nonce}\nIssued At: {issued_at}"
    )


def public_user(u: dict) -> dict:
    return {
        'address': u['address'],
        'username': u.get('username'),
        'character_id': u.get('character_id', 'arc'),
        'points': u.get('points', 0),
        'wins': u.get('wins', 0),
        'draws': u.get('draws', 0),
        'losses': u.get('losses', 0),
        'goals_for': u.get('goals_for', 0),
        'goals_against': u.get('goals_against', 0),
        'matches': u.get('matches', 0),
        'nft_token_id': u.get('active_token_id'),
        'nft_bonus': token_bonus(u['active_token_id']) if u.get('active_token_id') else None,
        'created_at': u.get('created_at').isoformat() if u.get('created_at') else None,
    }


def league_view(l: dict) -> dict:
    return {
        'id': l['id'],
        'round': l['round'],
        'opponents': l['opponents'],
        'results': l['results'],
        'finished': l['round'] >= len(ROUNDS),
        'standings': compute_standings(l),
    }


def compute_standings(l: dict):
    rows = []
    for i in range(6):
        rows.append({'idx': i, 'o': 0, 'g': 0, 'b': 0, 'm': 0, 'a': 0, 'y': 0, 'p': 0})
    for rnd in l['results']:
        for r in rnd:
            H = rows[r['home']]
            A = rows[r['away']]
            H['o'] += 1
            A['o'] += 1
            H['a'] += r['hs']
            H['y'] += r['as']
            A['a'] += r['as']
            A['y'] += r['hs']
            if r['hs'] > r['as']:
                H['g'] += 1
                A['m'] += 1
                H['p'] += 3
            elif r['hs'] < r['as']:
                A['g'] += 1
                H['m'] += 1
                A['p'] += 3
            else:
                H['b'] += 1
                A['b'] += 1
                H['p'] += 1
                A['p'] += 1
    for r in rows:
        r['av'] = r['a'] - r['y']
    rows.sort(key=lambda r: (-r['p'], -r['av'], -r['a'], r['idx']))
    return rows


app = FastAPI(title=APP_NAME)
api = APIRouter(prefix='/api')


# ---------- models ----------
class VerifyBody(BaseModel):
    address: str
    message: str
    signature: str


class UsernameBody(BaseModel):
    username: str


class CharacterBody(BaseModel):
    character_id: str


class MatchBody(BaseModel):
    mode: str = Field(pattern='^(league|quick|global)$')
    opponent_username: str
    opponent_char_id: str = 'mohawk'
    opponent_address: Optional[str] = None
    player_goals: int = Field(ge=0, le=50)
    opponent_goals: int = Field(ge=0, le=50)
    arena: str = 'paper'
    character_id: str = 'arc'


# ---------- auth helpers ----------
def make_token(address: str) -> str:
    return jwt.encode({'sub': address, 'exp': now() + timedelta(days=7)}, JWT_SECRET, algorithm='HS256')


async def current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(401, 'Not authenticated')
    token = authorization.split(' ', 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.PyJWTError:
        raise HTTPException(401, 'Invalid or expired token')
    user = await db.users.find_one({'address': payload['sub']})
    if not user:
        raise HTTPException(401, 'User not found')
    return user


# ---------- routes ----------
@api.get('/')
async def root():
    return {'message': 'Futbot League API', 'chain_id': CHAIN_ID}


@api.get('/auth/nonce')
async def get_nonce(address: str, domain: str):
    if not re.match(r'^0x[a-fA-F0-9]{40}$', address):
        raise HTTPException(400, 'Invalid address')
    if not re.match(r'^[a-zA-Z0-9.\-:]+$', domain):
        raise HTTPException(400, 'Invalid domain')
    nonce = secrets.token_hex(8)
    issued_at = now().replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    message = build_message(domain, address, nonce, issued_at)
    await db.nonces.insert_one({
        'nonce': nonce,
        'address': address.lower(),
        'message': message,
        'expires_at': now() + timedelta(minutes=5),
    })
    return {'nonce': nonce, 'message': message}


class ConnectBody(BaseModel):
    address: str


async def get_or_create_user(address: str) -> dict:
    user = await db.users.find_one({'address': address})
    if not user:
        user = {
            'id': str(uuid.uuid4()), 'address': address, 'username': None, 'character_id': 'arc',
            'points': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'goals_for': 0, 'goals_against': 0,
            'matches': 0, 'created_at': now(),
        }
        await db.users.insert_one(user)
    return await sync_active_token(user)


async def sync_active_token(user: dict) -> dict:
    """Best-effort: drop a sold NFT, auto-pick the first owned one when none is active."""
    try:
        owned = await owned_tokens(user['address'])
    except HTTPException:
        return user
    active = user.get('active_token_id')
    if active in owned:
        return user
    new_active = owned[0] if owned else None
    if new_active != active:
        await db.users.update_one({'address': user['address']}, {'$set': {'active_token_id': new_active}})
        user['active_token_id'] = new_active
    return user


class ActiveNftBody(BaseModel):
    token_id: int = Field(ge=1)


@api.get('/nft/mine')
async def my_nfts(user=Depends(current_user)):
    owned = await owned_tokens(user['address'])
    user = await sync_active_token(user)
    return {'tokens': [token_bonus(t) for t in owned if token_bonus(t)], 'active_token_id': user.get('active_token_id')}


@api.post('/nft/active')
async def set_active_nft(body: ActiveNftBody, user=Depends(current_user)):
    if body.token_id not in await owned_tokens(user['address']):
        raise HTTPException(403, 'This wallet does not own that GoalHoodz NFT.')
    if not token_bonus(body.token_id):
        raise HTTPException(404, 'Unknown token')
    await db.users.update_one({'address': user['address']}, {'$set': {'active_token_id': body.token_id}})
    return public_user(await db.users.find_one({'address': user['address']}))


@api.get('/nft/bonus/{token_id}')
async def nft_bonus(token_id: int):
    b = token_bonus(token_id)
    if not b:
        raise HTTPException(404, 'Unknown token')
    return b


@api.post('/auth/connect')
async def connect(body: ConnectBody):
    """Wallet connection = login (no extra signature step)."""
    if not re.match(r'^0x[a-fA-F0-9]{40}$', body.address):
        raise HTTPException(400, 'Invalid address')
    address = body.address.lower()
    user = await get_or_create_user(address)
    return {'token': make_token(address), 'user': public_user(user)}


@api.get('/nft/status')
async def nft_status(address: str):
    if not re.match(r'^0x[a-fA-F0-9]{40}$', address):
        raise HTTPException(400, 'Invalid address')
    bal = await nft_balance(address)
    return {'address': address.lower(), 'balance': bal, 'has_nft': bal > 0, **gate_info()}


@api.post('/auth/verify')
async def verify(body: VerifyBody):
    address = body.address.lower()
    pending = await db.nonces.find_one({'address': address, 'message': body.message, 'expires_at': {'$gt': now()}})
    if not pending:
        raise HTTPException(401, 'Unknown or expired sign-in request')
    try:
        recovered = Account.recover_message(encode_defunct(text=body.message), signature=body.signature)
    except Exception:
        raise HTTPException(401, 'Invalid signature')
    if recovered.lower() != address:
        raise HTTPException(401, 'Signer mismatch')
    await db.nonces.delete_one({'_id': pending['_id']})
    user = await get_or_create_user(address)
    return {'token': make_token(address), 'user': public_user(user)}


@api.get('/me')
async def me(user=Depends(current_user)):
    return public_user(user)


@api.put('/me/username')
async def set_username(body: UsernameBody, user=Depends(current_user)):
    name = body.username.strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, 'Username must be 3-16 chars: letters, numbers, underscore')
    taken = await db.users.find_one({'username_lc': name.lower(), 'address': {'$ne': user['address']}})
    if taken:
        raise HTTPException(409, 'Username already taken')
    await db.users.update_one({'address': user['address']}, {'$set': {'username': name, 'username_lc': name.lower()}})
    user = await db.users.find_one({'address': user['address']})
    return public_user(user)


@api.put('/me/character')
async def set_character(body: CharacterBody, user=Depends(current_user)):
    if body.character_id not in CHAR_IDS:
        raise HTTPException(400, 'Unknown character')
    await db.users.update_one({'address': user['address']}, {'$set': {'character_id': body.character_id}})
    user = await db.users.find_one({'address': user['address']})
    return public_user(user)


@api.get('/opponent')
async def opponent():
    return {'username': random_username(), 'char_id': random.choice(CHAR_IDS)}


async def get_or_create_league(address: str) -> dict:
    l = await db.leagues.find_one({'address': address, 'active': True})
    if l:
        return l
    names = set()
    opponents = []
    while len(opponents) < 5:
        n = random_username()
        if n in names:
            continue
        names.add(n)
        opponents.append({'username': n, 'char_id': random.choice(CHAR_IDS)})
    l = {'id': str(uuid.uuid4()), 'address': address, 'active': True, 'round': 0,
         'opponents': opponents, 'results': [], 'created_at': now()}
    await db.leagues.insert_one(l)
    return l


@api.get('/league')
async def get_league(user=Depends(current_user)):
    return league_view(await get_or_create_league(user['address']))


@api.post('/league/reset')
async def reset_league(user=Depends(current_user)):
    await db.leagues.update_many({'address': user['address'], 'active': True}, {'$set': {'active': False}})
    return league_view(await get_or_create_league(user['address']))


def stat_inc(gf: int, ga: int) -> dict:
    outcome = 'win' if gf > ga else 'loss' if gf < ga else 'draw'
    pts = 3 if outcome == 'win' else 1 if outcome == 'draw' else 0
    return {'points': pts, 'matches': 1, 'goals_for': gf, 'goals_against': ga,
            'wins': 1 if outcome == 'win' else 0, 'draws': 1 if outcome == 'draw' else 0,
            'losses': 1 if outcome == 'loss' else 0}


def team_view(u: dict) -> dict:
    return {'address': u['address'], 'username': u.get('username'), 'char_id': u.get('character_id', 'arc'), 'is_bot': False}


def global_match_view(m: dict) -> dict:
    return {
        'id': m['id'], 'home': m['home'], 'away': m['away'], 'home_goals': m['home_goals'], 'away_goals': m['away_goals'],
        'arena': m.get('arena', 'paper'), 'played_at': m['played_at'].isoformat() if isinstance(m.get('played_at'), datetime) else m.get('played_at'),
    }


@api.get('/global/opponent')
async def global_opponent(user=Depends(current_user)):
    """Every wallet is a team: pick another real wallet as the rival (least recently faced first)."""
    teams = await db.users.find({'username': {'$ne': None}, 'address': {'$ne': user['address']}}, {'_id': 0}).to_list(2000)
    if not teams:
        return {'address': None, 'username': random_username(), 'char_id': random.choice(CHAR_IDS), 'is_bot': True}
    recent = await db.league_matches.find({'home.address': user['address']}, {'_id': 0, 'away.address': 1}).sort('played_at', -1).to_list(50)
    faced = [m['away']['address'] for m in recent]
    fresh = [t for t in teams if t['address'] not in faced]
    if fresh:
        return team_view(random.choice(fresh))
    faced_rank = {a: i for i, a in enumerate(faced)}
    teams.sort(key=lambda t: -faced_rank.get(t['address'], 999))
    return team_view(random.choice(teams[: max(1, len(teams) // 2)]))


@api.get('/global/matches')
async def global_matches(limit: int = 20):
    limit = max(1, min(limit, 100))
    rows = await db.league_matches.find({}, {'_id': 0}).sort('played_at', -1).to_list(limit)
    return [global_match_view(m) for m in rows]


@api.get('/global/me')
async def my_global_matches(user=Depends(current_user)):
    rows = await db.league_matches.find(
        {'$or': [{'home.address': user['address']}, {'away.address': user['address']}]}, {'_id': 0}
    ).sort('played_at', -1).to_list(20)
    form = []
    for m in rows[:5]:
        mine, theirs = (m['home_goals'], m['away_goals']) if m['home']['address'] == user['address'] else (m['away_goals'], m['home_goals'])
        form.append('W' if mine > theirs else 'L' if mine < theirs else 'D')
    return {'matches': [global_match_view(m) for m in rows], 'form': form}


@api.post('/matches')
async def save_match(body: MatchBody, user=Depends(current_user)):
    pg, og = body.player_goals, body.opponent_goals
    outcome = 'win' if pg > og else 'loss' if pg < og else 'draw'
    pts = 3 if outcome == 'win' else 1 if outcome == 'draw' else 0
    match = {
        'id': str(uuid.uuid4()), 'address': user['address'], 'username': user.get('username'),
        'mode': body.mode, 'opponent_username': body.opponent_username, 'opponent_char_id': body.opponent_char_id,
        'player_goals': pg, 'opponent_goals': og, 'outcome': outcome, 'points': pts,
        'arena': body.arena, 'character_id': body.character_id, 'nft_token_id': user.get('active_token_id'), 'played_at': now(),
    }
    league = None
    global_match = None
    if body.mode == 'global':
        rival = None
        if body.opponent_address and re.match(r'^0x[a-fA-F0-9]{40}$', body.opponent_address) and body.opponent_address.lower() != user['address']:
            rival = await db.users.find_one({'address': body.opponent_address.lower()})
        away = team_view(rival) if rival else {'address': None, 'username': body.opponent_username, 'char_id': body.opponent_char_id, 'is_bot': True}
        gm = {'id': match['id'], 'home': team_view(user), 'away': away, 'home_goals': pg, 'away_goals': og,
              'arena': body.arena, 'played_at': match['played_at']}
        await db.league_matches.insert_one(gm)
        if rival:
            await db.users.update_one({'address': rival['address']}, {'$inc': stat_inc(og, pg)})
            match['opponent_address'] = rival['address']
        global_match = global_match_view(gm)
    if body.mode == 'league':
        l = await get_or_create_league(user['address'])
        if l['round'] < len(ROUNDS):
            pairs = ROUNDS[l['round']]
            arena = 'paper' if l['round'] % 2 == 0 else 'inverted'
            rnd = []
            for i, (h, a) in enumerate(pairs):
                rnd.append({'home': h, 'away': a,
                            'hs': pg if i == 0 else random_goals(),
                            'as': og if i == 0 else random_goals(), 'arena': arena})
            match['league_round'] = l['round']
            await db.leagues.update_one({'id': l['id']}, {'$set': {'round': l['round'] + 1}, '$push': {'results': rnd}})
            l = await db.leagues.find_one({'id': l['id']})
        league = league_view(l)
    await db.matches.insert_one(match)
    await db.users.update_one({'address': user['address']}, {'$inc': {
        'points': pts, 'matches': 1, 'goals_for': pg, 'goals_against': og,
        'wins': 1 if outcome == 'win' else 0, 'draws': 1 if outcome == 'draw' else 0,
        'losses': 1 if outcome == 'loss' else 0}})
    user = await db.users.find_one({'address': user['address']})
    match.pop('_id', None)
    match['played_at'] = match['played_at'].isoformat()
    return {'match': match, 'user': public_user(user), 'league': league, 'global_match': global_match}


@api.get('/matches/me')
async def my_matches(user=Depends(current_user)):
    rows = await db.matches.find({'address': user['address']}, {'_id': 0}).sort('played_at', -1).to_list(20)
    for r in rows:
        if isinstance(r.get('played_at'), datetime):
            r['played_at'] = r['played_at'].isoformat()
    return rows


@api.get('/leaderboard')
async def leaderboard(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.users.find({'username': {'$ne': None}, 'matches': {'$gt': 0}}, {'_id': 0}).to_list(1000)
    rows.sort(key=lambda u: (-u.get('points', 0), -(u.get('goals_for', 0) - u.get('goals_against', 0)), -u.get('goals_for', 0), -u.get('wins', 0)))
    out = []
    for i, u in enumerate(rows[:limit]):
        d = public_user(u)
        d['rank'] = i + 1
        d['goal_diff'] = d['goals_for'] - d['goals_against']
        d['address'] = d['address'][:6] + '...' + d['address'][-4:]
        out.append(d)
    return out


import early as early_module  # noqa: E402
import collab as collab_module  # noqa: E402

early_module.db = db
collab_module.db = db
app.include_router(api)
app.include_router(early_module.router)
app.include_router(collab_module.router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=['*'],
    allow_headers=['*'],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@app.on_event('startup')
async def ensure_indexes():
    await db.nonces.create_index('expires_at', expireAfterSeconds=0)
    await db.users.create_index('address', unique=True)
    await db.users.create_index('username_lc')
    await db.matches.create_index([('address', 1), ('played_at', -1)])
    await db.leagues.create_index([('address', 1), ('active', 1)])
    await db.league_matches.create_index([('played_at', -1)])
    await db.league_matches.create_index([('home.address', 1), ('played_at', -1)])
    await db.league_matches.create_index([('away.address', 1), ('played_at', -1)])
    await early_module.ensure_indexes()
    await collab_module.ensure_indexes()


@app.on_event('shutdown')
async def shutdown_db_client():
    client.close()

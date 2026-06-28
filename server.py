import tornado.ioloop
import tornado.web
import tornado.websocket
import os
import json
import random
import string
import time
import asyncio
import sqlite3
import re

PORT = 5000
DB_PATH = 'users.db'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        clerk_id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        display_name TEXT NOT NULL,
        profile_picture TEXT NOT NULL DEFAULT '',
        wins INTEGER NOT NULL DEFAULT 0,
        losses INTEGER NOT NULL DEFAULT 0,
        draws INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS friends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL,
        UNIQUE(from_id, to_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL,
        room_code TEXT NOT NULL DEFAULT '',
        from_name TEXT NOT NULL DEFAULT '',
        from_pic TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL
    )''')
    # Migrate existing tables — ignore errors if columns already exist
    for col_sql in [
        'ALTER TABLE users ADD COLUMN profile_picture TEXT NOT NULL DEFAULT ""',
        'ALTER TABLE users ADD COLUMN wins INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE users ADD COLUMN losses INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE users ADD COLUMN draws INTEGER NOT NULL DEFAULT 0',
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    conn.commit()
    conn.close()


init_db()

rooms = {}


def make_code():
    return ''.join(random.choices(string.ascii_uppercase, k=4))


def check_winner(board):
    wins = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]]
    for combo in wins:
        a, b, c = combo
        if board[a] and board[a] == board[b] == board[c]:
            return board[a], combo
    return None, None


def safe_send(ws, msg):
    try:
        if ws and ws.ws_connection:
            ws.write_message(msg)
    except Exception:
        pass


def broadcast(room, msg):
    for player in room['players']:
        if player:
            safe_send(player, msg)


def get_profile(clerk_id):
    if not clerk_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        'SELECT username, display_name, profile_picture, wins, losses, draws FROM users WHERE clerk_id = ?',
        (clerk_id,)
    ).fetchone()
    conn.close()
    if row:
        return {
            'id': clerk_id,
            'username': row[0], 'displayName': row[1], 'profilePicture': row[2],
            'wins': row[3], 'losses': row[4], 'draws': row[5],
        }
    return None


def record_game_result(winner_id, loser_id, is_draw, x_id, o_id):
    """Update wins/losses/draws for both players after a finished game."""
    conn = sqlite3.connect(DB_PATH)
    try:
        if is_draw:
            for pid in (x_id, o_id):
                if pid:
                    conn.execute('UPDATE users SET draws = draws + 1 WHERE clerk_id = ?', (pid,))
        else:
            if winner_id:
                conn.execute('UPDATE users SET wins = wins + 1 WHERE clerk_id = ?', (winner_id,))
            if loser_id:
                conn.execute('UPDATE users SET losses = losses + 1 WHERE clerk_id = ?', (loser_id,))
        conn.commit()
    finally:
        conn.close()


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1" />
  <title>Tic Tac Toe</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background: #111;
      color: #fff;
      min-height: 100dvh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 32px 24px;
    }
    .wrap { width: 100%; max-width: 380px; display: flex; flex-direction: column; align-items: center; gap: 20px; }
    .logo { width: 68px; height: 68px; border-radius: 18px; }
    h1 { font-size: 2rem; font-weight: 700; letter-spacing: 0.18em; color: #22d3ee; text-transform: uppercase; text-align: center; }
    .sub { font-size: 0.88rem; color: #555; text-align: center; line-height: 1.5; }
    .btns { width: 100%; display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
    .btn { display: block; width: 100%; text-align: center; font-family: inherit; font-size: 0.95rem; font-weight: 600; padding: 14px 20px; border-radius: 12px; cursor: pointer; text-decoration: none; transition: opacity 0.15s, border-color 0.15s, color 0.15s; }
    .btn-primary { background: #22d3ee; color: #111; border: none; }
    .btn-primary:hover { opacity: 0.88; }
    .btn-secondary { background: transparent; color: #ccc; border: 1px solid #2a2a2a; }
    .btn-secondary:hover { border-color: #444; color: #fff; }
    .btn-ghost { background: transparent; color: #444; border: none; font-size: 0.85rem; font-weight: 500; padding: 10px 20px; }
    .btn-ghost:hover { color: #888; }
    .note { font-size: 0.75rem; color: #333; text-align: center; line-height: 1.6; }
  </style>
</head>
<body>
  <div class="wrap">
    <img class="logo" src="/logo.svg" alt="Tic Tac Toe" onerror="this.style.display='none'" />
    <h1>Tic Tac Toe</h1>
    <p class="sub">Local &amp; online multiplayer, AI opponents, voice chat</p>
    <div class="btns">
      <a class="btn btn-primary" href="/sign-in">Sign in with Google to Play</a>
      <a class="btn btn-secondary" href="/sign-up">Create Account</a>
      <a class="btn btn-ghost" href="/guest.html">Play as Guest</a>
    </div>
    <p class="note">A Google account is required to track your scores<br>and play online</p>
  </div>
  <div style="position:fixed;top:12px;right:16px;font-family:Inter,sans-serif;font-size:0.72rem;font-weight:500;color:#333;letter-spacing:0.04em;pointer-events:none;">Made by Safaan</div>
  <script>
    var jc = new URLSearchParams(location.search).get('join');
    if (jc && /^[A-Z]{4}$/i.test(jc)) localStorage.setItem('ttt_join', jc.toUpperCase());
  </script>
</body>
</html>"""


class GameWebSocketHandler(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin):
        return True

    def open(self):
        self.room_code = None
        self.role = None
        user_id = self.get_argument('userId', '').strip()
        self.user_id = user_id
        self.profile = get_profile(user_id) if user_id else None

    def on_message(self, message):
        try:
            data = json.loads(message)
        except Exception:
            return
        msg_type = data.get('type')
        if msg_type == 'create':
            self._handle_create()
        elif msg_type == 'join':
            self._handle_join(data.get('code', ''))
        elif msg_type == 'move':
            self._handle_move(data.get('index'))
        elif msg_type == 'restart':
            self._handle_restart()
        elif msg_type == 'chat':
            self._handle_chat(data.get('text', ''))
        elif msg_type in ('rtc_offer', 'rtc_answer', 'rtc_ice', 'rtc_ready'):
            self._relay_signal(data)

    def _handle_create(self):
        for _ in range(20):
            code = make_code()
            if code not in rooms:
                break
        rooms[code] = {
            'board': [None] * 9,
            'turn': 'X',
            'players': [self, None],
            'winner': None,
            'draw': False,
            'restart_votes': set(),
            'last_activity': time.time(),
        }
        self.room_code = code
        self.role = 'X'
        safe_send(self, json.dumps({'type': 'created', 'code': code}))

    def _handle_join(self, code):
        code = code.upper().strip()
        room = rooms.get(code)
        if not room:
            safe_send(self, json.dumps({'type': 'error', 'message': 'Room not found'}))
            return
        if room['players'][1] is not None:
            safe_send(self, json.dumps({'type': 'error', 'message': 'Room is full'}))
            return
        room['players'][1] = self
        self.room_code = code
        self.role = 'O'
        room['last_activity'] = time.time()
        safe_send(self, json.dumps({'type': 'joined', 'code': code}))
        x_player = room['players'][0]
        o_player = room['players'][1]
        start_msg = json.dumps({
            'type': 'start',
            'board': room['board'],
            'currentPlayer': room['turn'],
            'xProfile': x_player.profile if x_player else None,
            'oProfile': o_player.profile if o_player else None,
        })
        broadcast(room, start_msg)

    def _handle_move(self, index):
        if index is None:
            return
        code = self.room_code
        room = rooms.get(code)
        if not room:
            return
        board = room['board']
        if room['winner'] or room['draw']:
            return
        if room['turn'] != self.role:
            return
        if board[index] is not None:
            return
        board[index] = self.role
        room['last_activity'] = time.time()
        winner, line = check_winner(board)
        draw = not winner and all(v is not None for v in board)
        if winner or draw:
            room['winner'] = winner
            room['draw'] = draw
            # Determine player IDs for stat recording
            x_player = room['players'][0]
            o_player = room['players'][1]
            x_id = x_player.user_id if x_player else None
            o_id = o_player.user_id if o_player else None
            winner_id = x_id if winner == 'X' else (o_id if winner == 'O' else None)
            loser_id  = o_id if winner == 'X' else (x_id if winner == 'O' else None)
            record_game_result(winner_id, loser_id, bool(draw), x_id, o_id)
            # Refresh profiles so restart messages carry updated stats
            if x_player and x_player.user_id:
                x_player.profile = get_profile(x_player.user_id)
            if o_player and o_player.user_id:
                o_player.profile = get_profile(o_player.user_id)
            broadcast(room, json.dumps({'type': 'gameover', 'board': board, 'winner': winner, 'line': line}))
        else:
            room['turn'] = 'O' if self.role == 'X' else 'X'
            broadcast(room, json.dumps({'type': 'update', 'board': board, 'currentPlayer': room['turn']}))

    def _handle_restart(self):
        code = self.room_code
        room = rooms.get(code)
        if not room:
            return
        room['restart_votes'].add(self.role)
        if len(room['restart_votes']) < 2:
            for player in room['players']:
                if player and player != self:
                    safe_send(player, json.dumps({'type': 'restart_waiting'}))
        else:
            room['board'] = [None] * 9
            room['turn'] = 'X'
            room['winner'] = None
            room['draw'] = False
            room['restart_votes'] = set()
            room['last_activity'] = time.time()
            x_player = room['players'][0]
            o_player = room['players'][1]
            broadcast(room, json.dumps({
                'type': 'start',
                'board': room['board'],
                'currentPlayer': room['turn'],
                'xProfile': x_player.profile if x_player else None,
                'oProfile': o_player.profile if o_player else None,
            }))

    def _handle_chat(self, text):
        code = self.room_code
        room = rooms.get(code)
        if not room:
            return
        broadcast(room, json.dumps({'type': 'chat', 'from': self.role, 'text': text}))

    def _relay_signal(self, data):
        code = self.room_code
        room = rooms.get(code)
        if not room:
            return
        msg = json.dumps(data)
        for player in room['players']:
            if player and player != self:
                safe_send(player, msg)

    def on_close(self):
        code = self.room_code
        room = rooms.get(code)
        if not room:
            return
        for player in room['players']:
            if player and player != self:
                safe_send(player, json.dumps({'type': 'opponent_left', 'reason': 'Opponent disconnected.'}))
        rooms.pop(code, None)


class UserStatusHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def get(self):
        user_id = self.get_argument('userId', '').strip()
        if not user_id:
            self.set_status(400)
            self.write(json.dumps({'error': 'userId required'}))
            return
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            'SELECT username, display_name, profile_picture FROM users WHERE clerk_id = ?', (user_id,)
        ).fetchone()
        conn.close()
        if row:
            self.write(json.dumps({'hasProfile': True, 'username': row[0], 'displayName': row[1], 'profilePicture': row[2]}))
        else:
            self.write(json.dumps({'hasProfile': False}))


class UserCheckUsernameHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def get(self):
        username = self.get_argument('username', '').strip()
        if not username or not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
            self.write(json.dumps({'available': False}))
            return
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute('SELECT 1 FROM users WHERE username = ? COLLATE NOCASE', (username,)).fetchone()
        conn.close()
        self.write(json.dumps({'available': row is None}))


class UserRegisterHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def post(self):
        try:
            data = json.loads(self.request.body)
        except Exception:
            data = {}
        user_id = data.get('userId', '').strip()
        username = data.get('username', '').strip()
        display_name = data.get('displayName', '').strip()
        profile_picture = data.get('profilePicture', '').strip()

        if not user_id or not username or not display_name:
            self.set_status(400)
            self.write(json.dumps({'error': 'All fields required'}))
            return
        if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
            self.set_status(400)
            self.write(json.dumps({'error': 'Username must be 3–20 characters: letters, numbers, underscores only'}))
            return
        if len(display_name) > 30:
            self.set_status(400)
            self.write(json.dumps({'error': 'Display name too long'}))
            return

        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                'INSERT INTO users (clerk_id, username, display_name, profile_picture, created_at) VALUES (?, ?, ?, ?, ?)',
                (user_id, username, display_name, profile_picture, int(time.time()))
            )
            conn.commit()
            self.write(json.dumps({'ok': True}))
        except sqlite3.IntegrityError:
            self.set_status(400)
            self.write(json.dumps({'error': 'Username has been taken'}))
        finally:
            conn.close()


class UserListHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def get(self):
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            'SELECT clerk_id, username, display_name, profile_picture, wins, losses, draws FROM users ORDER BY wins DESC, created_at DESC'
        ).fetchall()
        conn.close()
        users = [{'id': r[0], 'username': r[1], 'displayName': r[2], 'profilePicture': r[3], 'wins': r[4], 'losses': r[5], 'draws': r[6]} for r in rows]
        self.write(json.dumps(users))


class FriendRequestHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def post(self):
        try:
            data = json.loads(self.request.body)
        except Exception:
            data = {}
        from_id = data.get('fromId', '').strip()
        to_id = data.get('toId', '').strip()
        if not from_id or not to_id or from_id == to_id:
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid request'}))
            return
        conn = sqlite3.connect(DB_PATH)
        try:
            existing = conn.execute(
                'SELECT status FROM friends WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)',
                (from_id, to_id, to_id, from_id)
            ).fetchone()
            if existing:
                self.write(json.dumps({'error': 'Already friends or request pending', 'status': existing[0]}))
                return
            conn.execute(
                'INSERT INTO friends (from_id, to_id, status, created_at) VALUES (?, ?, "pending", ?)',
                (from_id, to_id, int(time.time()))
            )
            conn.commit()
            self.write(json.dumps({'ok': True}))
        except sqlite3.IntegrityError:
            self.write(json.dumps({'error': 'Request already sent'}))
        finally:
            conn.close()


class FriendRespondHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def post(self):
        try:
            data = json.loads(self.request.body)
        except Exception:
            data = {}
        user_id = data.get('userId', '').strip()
        from_id = data.get('fromId', '').strip()
        accept = data.get('accept', False)
        if not user_id or not from_id:
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid request'}))
            return
        conn = sqlite3.connect(DB_PATH)
        try:
            if accept:
                conn.execute(
                    'UPDATE friends SET status="accepted" WHERE from_id=? AND to_id=? AND status="pending"',
                    (from_id, user_id)
                )
            else:
                conn.execute(
                    'DELETE FROM friends WHERE from_id=? AND to_id=? AND status="pending"',
                    (from_id, user_id)
                )
            conn.commit()
            self.write(json.dumps({'ok': True}))
        finally:
            conn.close()


class FriendListHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def get(self):
        user_id = self.get_argument('userId', '').strip()
        if not user_id:
            self.set_status(400)
            self.write(json.dumps({'error': 'userId required'}))
            return
        conn = sqlite3.connect(DB_PATH)
        # Friends (accepted)
        friends_rows = conn.execute('''
            SELECT u.clerk_id, u.username, u.display_name, u.profile_picture, u.wins, u.losses, u.draws
            FROM friends f JOIN users u ON (
                CASE WHEN f.from_id = ? THEN f.to_id ELSE f.from_id END = u.clerk_id
            )
            WHERE (f.from_id = ? OR f.to_id = ?) AND f.status = "accepted"
        ''', (user_id, user_id, user_id)).fetchall()
        # Incoming pending requests
        incoming_rows = conn.execute('''
            SELECT u.clerk_id, u.username, u.display_name, u.profile_picture, u.wins, u.losses, u.draws
            FROM friends f JOIN users u ON f.from_id = u.clerk_id
            WHERE f.to_id = ? AND f.status = "pending"
        ''', (user_id,)).fetchall()
        # Outgoing pending
        outgoing_rows = conn.execute('''
            SELECT to_id FROM friends WHERE from_id = ? AND status = "pending"
        ''', (user_id,)).fetchall()
        conn.close()

        def row_to_dict(r):
            return {'id': r[0], 'username': r[1], 'displayName': r[2], 'profilePicture': r[3], 'wins': r[4], 'losses': r[5], 'draws': r[6]}

        self.write(json.dumps({
            'friends': [row_to_dict(r) for r in friends_rows],
            'incoming': [row_to_dict(r) for r in incoming_rows],
            'outgoing': [r[0] for r in outgoing_rows],
        }))


class ChallengeSendHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def post(self):
        try:
            data = json.loads(self.request.body)
        except Exception:
            data = {}
        from_id   = data.get('fromId', '').strip()
        to_id     = data.get('toId', '').strip()
        room_code = data.get('roomCode', '').strip().upper()
        from_name = data.get('fromName', '').strip()
        from_pic  = data.get('fromPic', '').strip()
        if not from_id or not to_id or not room_code:
            self.set_status(400)
            self.write(json.dumps({'error': 'fromId, toId and roomCode required'}))
            return
        conn = sqlite3.connect(DB_PATH)
        try:
            # Remove any previous pending challenge between these two users
            conn.execute(
                'DELETE FROM challenges WHERE from_id=? AND to_id=? AND status="pending"',
                (from_id, to_id)
            )
            conn.execute(
                'INSERT INTO challenges (from_id, to_id, room_code, from_name, from_pic, status, created_at) VALUES (?,?,?,?,?,"pending",?)',
                (from_id, to_id, room_code, from_name, from_pic, int(time.time()))
            )
            conn.commit()
            self.write(json.dumps({'ok': True}))
        finally:
            conn.close()


class ChallengeIncomingHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def get(self):
        user_id = self.get_argument('userId', '').strip()
        if not user_id:
            self.set_status(400)
            self.write(json.dumps({'error': 'userId required'}))
            return
        cutoff = int(time.time()) - 300  # 5-minute expiry
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            '''SELECT id, from_id, room_code, from_name, from_pic, created_at
               FROM challenges
               WHERE to_id=? AND status="pending" AND room_code != "" AND created_at > ?
               ORDER BY created_at DESC LIMIT 5''',
            (user_id, cutoff)
        ).fetchall()
        conn.close()
        challenges = [
            {'id': r[0], 'fromId': r[1], 'roomCode': r[2], 'fromName': r[3], 'fromPic': r[4], 'createdAt': r[5]}
            for r in rows
        ]
        self.write(json.dumps(challenges))


class ChallengeDismissHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')

    def post(self):
        try:
            data = json.loads(self.request.body)
        except Exception:
            data = {}
        challenge_id = data.get('challengeId')
        if not challenge_id:
            self.set_status(400)
            self.write(json.dumps({'error': 'challengeId required'}))
            return
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute('UPDATE challenges SET status="dismissed" WHERE id=?', (challenge_id,))
            conn.commit()
            self.write(json.dumps({'ok': True}))
        finally:
            conn.close()


class LandingHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Content-Type', 'text/html; charset=utf-8')
        self.set_header('Cache-Control', 'no-cache')
        self.write(LANDING_HTML)


class ReactAppHandler(tornado.web.RequestHandler):
    def get(self, path=None):
        self.set_header('Cache-Control', 'no-cache')
        self.set_header('Content-Type', 'text/html; charset=utf-8')
        with open('index.html', 'rb') as f:
            self.write(f.read())


class GuestHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Cache-Control', 'no-cache')
        self.set_header('Content-Type', 'text/html; charset=utf-8')
        with open('guest.html', 'rb') as f:
            self.write(f.read())


class StaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header('Cache-Control', 'no-cache')


def make_app():
    return tornado.web.Application([
        (r'/', ReactAppHandler),
        (r'/welcome', LandingHandler),
        (r'/guest\.html', GuestHandler),
        (r'/sign-in(?:/.*)?', ReactAppHandler),
        (r'/sign-up(?:/.*)?', ReactAppHandler),
        (r'/api/ws', GameWebSocketHandler),
        (r'/api/user/status', UserStatusHandler),
        (r'/api/user/check-username', UserCheckUsernameHandler),
        (r'/api/user/register', UserRegisterHandler),
        (r'/api/users', UserListHandler),
        (r'/api/friends', FriendListHandler),
        (r'/api/friends/request', FriendRequestHandler),
        (r'/api/friends/respond', FriendRespondHandler),
        (r'/api/challenges/send', ChallengeSendHandler),
        (r'/api/challenges/incoming', ChallengeIncomingHandler),
        (r'/api/challenges/dismiss', ChallengeDismissHandler),
        (r'/(.*)', StaticFileHandler, {'path': os.path.dirname(os.path.abspath(__file__))}),
    ])


async def cleanup_loop():
    while True:
        await asyncio.sleep(300)
        now = time.time()
        stale = [c for c, r in list(rooms.items()) if now - r['last_activity'] > 1800]
        for c in stale:
            rooms.pop(c, None)


if __name__ == '__main__':
    app = make_app()
    app.listen(PORT)
    print(f'Serving on port {PORT}')
    loop = tornado.ioloop.IOLoop.current()
    loop.asyncio_loop.create_task(cleanup_loop())
    loop.start()

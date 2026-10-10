import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / 'chess_assistant.db'


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                history TEXT NOT NULL DEFAULT '',
                user_color TEXT NOT NULL DEFAULT 'w',
                start_fen TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def list_games():
    with _connect() as conn:
        rows = conn.execute(
            '''
            SELECT id, name, history, user_color, start_fen, created_at, updated_at
            FROM games
            ORDER BY updated_at DESC, id DESC
            '''
        ).fetchall()
    return [dict(row) for row in rows]


def get_game(game_id):
    with _connect() as conn:
        row = conn.execute(
            '''
            SELECT id, name, history, user_color, start_fen, created_at, updated_at
            FROM games
            WHERE id = ?
            ''',
            (game_id,),
        ).fetchone()
    return dict(row) if row else None


def create_game(name, history, user_color, start_fen):
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            '''
            INSERT INTO games (name, history, user_color, start_fen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (name, history or '', user_color, start_fen, now, now),
        )
        conn.commit()
        return cur.lastrowid


def update_game_history(game_id, history):
    with _connect() as conn:
        conn.execute(
            '''
            UPDATE games
            SET history = ?, updated_at = ?
            WHERE id = ?
            ''',
            (history or '', _now(), game_id),
        )
        conn.commit()


def delete_game(game_id):
    with _connect() as conn:
        cur = conn.execute('DELETE FROM games WHERE id = ?', (game_id,))
        conn.commit()
        return cur.rowcount > 0

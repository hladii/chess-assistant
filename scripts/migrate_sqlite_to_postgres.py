#!/usr/bin/env python3
"""Copy games from local SQLite (chess_assistant.db) into PostgreSQL.

Usage:
  export DATABASE_URL=postgresql://chess:chess@127.0.0.1:5432/chess_assistant
  python scripts/migrate_sqlite_to_postgres.py
  python scripts/migrate_sqlite_to_postgres.py --sqlite /path/to/chess_assistant.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import init_db, seed_default_user, upsert_game  # noqa: E402


def read_sqlite_games(sqlite_path):
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT id, name, history, user_color, start_fen, created_at, updated_at
            FROM games
            ORDER BY id
            '''
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def main():
    parser = argparse.ArgumentParser(description='Migrate SQLite games to PostgreSQL')
    parser.add_argument(
        '--sqlite',
        default=str(ROOT / 'chess_assistant.db'),
        help='Path to chess_assistant.db',
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f'SQLite file not found: {sqlite_path}')

    games = read_sqlite_games(sqlite_path)
    print(f'Read {len(games)} game(s) from {sqlite_path}')

    init_db()
    user_id = seed_default_user()
    print(f'Using seed user_id={user_id}')
    for game in games:
        upsert_game(
            game_id=game['id'],
            name=game['name'],
            history=game['history'] or '',
            user_color=game['user_color'] or 'w',
            start_fen=game['start_fen'],
            created_at=game['created_at'],
            updated_at=game['updated_at'],
            user_id=user_id,
        )
        print(f"  upserted id={game['id']} name={game['name']!r} user_id={user_id}")

    print('Done.')


if __name__ == '__main__':
    main()

"""Seed curated tactical puzzles into the catalog."""

from __future__ import annotations

import json
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parent / 'puzzles_data.json'


def load_seed_puzzles():
    if not SEED_PATH.is_file():
        return []
    with SEED_PATH.open(encoding='utf-8') as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        return []
    return data


def seed_puzzles(upsert_fn):
    """Call upsert_fn(puzzle_dict) for each seed entry. Returns inserted/updated count."""
    count = 0
    for item in load_seed_puzzles():
        if not item.get('slug') or not item.get('fen') or not item.get('solution_sans'):
            continue
        upsert_fn(item)
        count += 1
    return count

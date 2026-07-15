"""Connection helpers for the HQ, region, and club SQLite files."""

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # dict-style rows: row["id"]
    return conn


def get_hq_connection():
    return _connect(DATA_DIR / "hq.db")


def connect_to(db_path):
    return _connect(db_path)

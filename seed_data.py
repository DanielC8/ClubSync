"""Builds the demo HQ/region/club SQLite files with injected inconsistencies."""

import shutil
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

# region_id -> club_ids in that region
NETWORK = {1: [1, 2], 2: [3]}
MEMBERS_PER_CLUB = 9


def _fresh_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _create_users_table(conn):
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, "
        "is_active INTEGER, is_deleted INTEGER)"
    )


def _create_members_table(conn):
    conn.execute(
        "CREATE TABLE members (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, region_id INTEGER, club_id INTEGER)"
    )


def _create_member_details_table(conn):
    conn.execute(
        "CREATE TABLE member_details (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER)"
    )


def _create_leaders_table(conn):
    conn.execute(
        "CREATE TABLE leaders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, club_id INTEGER)"
    )


def _create_registry_table(conn):
    conn.execute(
        "CREATE TABLE db_registry (region_id INTEGER, club_id INTEGER, "
        "region_db_name TEXT, club_db_name TEXT, db_path TEXT)"
    )


def build_all(reset=True):
    """Rebuild data/, write the db_registry, print the injected errors. With
    reset=True it wipes and recreates data/."""
    if reset and DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    hq_conn = _fresh_conn(DATA_DIR / "hq.db")
    _create_users_table(hq_conn)
    _create_members_table(hq_conn)
    _create_leaders_table(hq_conn)
    _create_registry_table(hq_conn)

    region_conns = {}
    for region_id in NETWORK:
        path = DATA_DIR / f"region_{region_id}.db"
        conn = _fresh_conn(path)
        _create_users_table(conn)
        _create_members_table(conn)
        _create_leaders_table(conn)
        region_conns[region_id] = conn
        hq_conn.execute(
            "INSERT INTO db_registry "
            "(region_id, club_id, region_db_name, club_db_name, db_path) "
            "VALUES (?, NULL, ?, NULL, ?)",
            (region_id, path.name, str(path)),
        )

    club_conns = {}
    club_region = {}
    for region_id, club_ids in NETWORK.items():
        for club_id in club_ids:
            path = DATA_DIR / f"club_{club_id}.db"
            conn = _fresh_conn(path)
            _create_users_table(conn)
            _create_member_details_table(conn)
            _create_leaders_table(conn)
            club_conns[club_id] = conn
            club_region[club_id] = region_id
            hq_conn.execute(
                "INSERT INTO db_registry "
                "(region_id, club_id, region_db_name, club_db_name, db_path) "
                "VALUES (?, ?, NULL, ?, ?)",
                (region_id, club_id, path.name, str(path)),
            )

    next_id = 1
    members = []  # (id, name, is_active, is_deleted, region_id, club_id)
    leaders_only = []  # (id, name, is_active, is_deleted, region_id, club_id)
    for club_id, region_id in club_region.items():
        for _ in range(MEMBERS_PER_CLUB):
            members.append((next_id, f"Member {next_id}", 1, 0, region_id, club_id))
            next_id += 1
        leaders_only.append((next_id, f"Leader {next_id}", 1, 0, region_id, club_id))
        next_id += 1

    hq_conn.executemany(
        "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, ?, ?)",
        [(uid, name, a, d) for uid, name, a, d, _, _ in members + leaders_only],
    )
    hq_conn.executemany(
        "INSERT INTO members (user_id, region_id, club_id) VALUES (?, ?, ?)",
        [(uid, rid, cid) for uid, _, _, _, rid, cid in members],
    )
    hq_conn.executemany(
        "INSERT INTO leaders (user_id, club_id) VALUES (?, ?)",
        [(uid, cid) for uid, _, _, _, _, cid in leaders_only],
    )

    for region_id, conn in region_conns.items():
        region_people = [row for row in members + leaders_only if row[4] == region_id]
        conn.executemany(
            "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, ?, ?)",
            [(uid, name, a, d) for uid, name, a, d, _, _ in region_people],
        )
        conn.executemany(
            "INSERT INTO members (user_id, region_id, club_id) VALUES (?, ?, ?)",
            [(uid, rid, cid) for uid, _, _, _, rid, cid in members if rid == region_id],
        )
        conn.executemany(
            "INSERT INTO leaders (user_id, club_id) VALUES (?, ?)",
            [(uid, cid) for uid, _, _, _, rid, cid in leaders_only if rid == region_id],
        )

    for club_id, conn in club_conns.items():
        club_members = [row for row in members if row[5] == club_id]
        club_leaders = [row for row in leaders_only if row[5] == club_id]
        conn.executemany(
            "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, ?, ?)",
            [(uid, name, a, d) for uid, name, a, d, _, _ in club_members + club_leaders],
        )
        conn.executemany(
            "INSERT INTO member_details (user_id) VALUES (?)",
            [(uid,) for uid, _, _, _, _, _ in club_members],
        )
        conn.executemany(
            "INSERT INTO leaders (user_id, club_id) VALUES (?, ?)",
            [(uid, cid) for uid, _, _, _, _, cid in club_leaders],
        )

    # ---- inject the five known problems (see BUILD_SPEC §7) ----
    injected = {}

    sanity_uid = members[0][0]
    hq_conn.execute(
        "UPDATE users SET is_active = 1, is_deleted = 1 WHERE id = ?", (sanity_uid,)
    )
    injected["sanity_violation"] = [sanity_uid]

    mismatch_uid, _, _, _, _, mismatch_club = members[1]
    hq_conn.execute(
        "UPDATE users SET is_active = 0, is_deleted = 1 WHERE id = ?", (mismatch_uid,)
    )
    club_conns[mismatch_club].execute(
        "UPDATE users SET is_active = 1, is_deleted = 0 WHERE id = ?", (mismatch_uid,)
    )
    injected["flag_mismatch"] = [mismatch_uid]

    ghost_uid, ghost_club = next_id, 1
    next_id += 1
    club_conns[ghost_club].execute(
        "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, 1, 0)",
        (ghost_uid, f"Ghost {ghost_uid}"),
    )
    club_conns[ghost_club].execute(
        "INSERT INTO member_details (user_id) VALUES (?)", (ghost_uid,)
    )
    injected["missing_in_hq"] = [ghost_uid]

    orphan_uid = next_id
    next_id += 1
    hq_conn.execute(
        "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, 1, 0)",
        (orphan_uid, f"Orphan {orphan_uid}"),
    )
    injected["orphan_user"] = [orphan_uid]

    gap_uid, gap_region, gap_club = next_id, 1, 2
    next_id += 1
    hq_conn.execute(
        "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, 1, 0)",
        (gap_uid, f"Gap {gap_uid}"),
    )
    hq_conn.execute(
        "INSERT INTO members (user_id, region_id, club_id) VALUES (?, ?, ?)",
        (gap_uid, gap_region, gap_club),
    )
    region_conns[gap_region].execute(
        "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, 1, 0)",
        (gap_uid, f"Gap {gap_uid}"),
    )
    region_conns[gap_region].execute(
        "INSERT INTO members (user_id, region_id, club_id) VALUES (?, ?, ?)",
        (gap_uid, gap_region, gap_club),
    )
    # intentionally NOT written to the club db — that's the cross-tier gap
    injected["cross_tier_gap"] = [gap_uid]

    hq_conn.commit()
    hq_conn.close()
    for conn in region_conns.values():
        conn.commit()
        conn.close()
    for conn in club_conns.values():
        conn.commit()
        conn.close()

    print("ClubSync seed data built in", DATA_DIR)
    print("Injected errors:")
    for kind, ids in injected.items():
        print(f"  {kind}: {ids}")

    return injected


if __name__ == "__main__":
    build_all(reset=True)

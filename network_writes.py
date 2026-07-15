"""Add-and-propagate layer: next_user_id(), add_member(), add_leader()."""

from db_connection import connect_to, get_hq_connection

NEW_USER_FLAGS = (1, 0)  # is_active, is_deleted


def next_user_id(hq_conn):
    # HQ allocates ids so every tier shares the same one
    row = hq_conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM users").fetchone()
    return row["next_id"]


def _resolve_club(hq_conn, club_id):
    """Region id + the region/club db paths for a club."""
    club = hq_conn.execute(
        "SELECT region_id, db_path FROM db_registry "
        "WHERE club_id = ? AND club_db_name IS NOT NULL",
        (club_id,),
    ).fetchone()
    if club is None:
        raise ValueError(f"club {club_id} isn't in the registry")

    region_id = club["region_id"]
    region = hq_conn.execute(
        "SELECT db_path FROM db_registry "
        "WHERE region_id = ? AND region_db_name IS NOT NULL",
        (region_id,),
    ).fetchone()
    if region is None:
        raise ValueError(f"region {region_id} isn't in the registry")

    return region_id, region["db_path"], club["db_path"]


def _insert_user(conn, user_id, name):
    active, deleted = NEW_USER_FLAGS
    conn.execute(
        "INSERT INTO users (id, name, is_active, is_deleted) VALUES (?, ?, ?, ?)",
        (user_id, name, active, deleted),
    )


def _add_person(name, club_id, propagate_to_club, as_leader):
    hq_conn = get_hq_connection()
    try:
        region_id, region_path, club_path = _resolve_club(hq_conn, club_id)
        new_id = next_user_id(hq_conn)

        # HQ first since it owns the id
        _insert_user(hq_conn, new_id, name)
        if as_leader:
            hq_conn.execute(
                "INSERT INTO leaders (user_id, club_id) VALUES (?, ?)", (new_id, club_id)
            )
        else:
            hq_conn.execute(
                "INSERT INTO members (user_id, region_id, club_id) VALUES (?, ?, ?)",
                (new_id, region_id, club_id),
            )
        hq_conn.commit()
        tiers = ["hq"]
    finally:
        hq_conn.close()

    region_conn = connect_to(region_path)
    try:
        _insert_user(region_conn, new_id, name)
        if as_leader:
            region_conn.execute(
                "INSERT INTO leaders (user_id, club_id) VALUES (?, ?)", (new_id, club_id)
            )
        else:
            region_conn.execute(
                "INSERT INTO members (user_id, region_id, club_id) VALUES (?, ?, ?)",
                (new_id, region_id, club_id),
            )
        region_conn.commit()
        tiers.append(f"region_{region_id}")
    finally:
        region_conn.close()

    # skipping the club write is the "skip club tier" option — leaves a gap the
    # next check run catches
    if propagate_to_club:
        club_conn = connect_to(club_path)
        try:
            _insert_user(club_conn, new_id, name)
            if as_leader:
                club_conn.execute(
                    "INSERT INTO leaders (user_id, club_id) VALUES (?, ?)", (new_id, club_id)
                )
            else:
                club_conn.execute(
                    "INSERT INTO member_details (user_id) VALUES (?)", (new_id,)
                )
            club_conn.commit()
            tiers.append(f"club_{club_id}")
        finally:
            club_conn.close()

    return new_id, tiers


def add_member(name, club_id, propagate_to_club=True):
    return _add_person(name, club_id, propagate_to_club, as_leader=False)


def add_leader(name, club_id, propagate_to_club=True):
    return _add_person(name, club_id, propagate_to_club, as_leader=True)


def update_user_flags(user_id, is_active, is_deleted):
    """Set a user's flags in HQ only. The region/club copies keep their old flags,
    so they now disagree with HQ and the next run catches the mismatch."""
    hq_conn = get_hq_connection()
    try:
        if hq_conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise ValueError(f"user {user_id} isn't in HQ")
        hq_conn.execute(
            "UPDATE users SET is_active = ?, is_deleted = ? WHERE id = ?",
            (is_active, is_deleted, user_id),
        )
        hq_conn.commit()
    finally:
        hq_conn.close()

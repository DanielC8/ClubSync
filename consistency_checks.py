"""Per-table consistency checks against the HQ users table."""

from db_connection import get_hq_connection
from utils import add_to_df

BATCH_SIZE = 500


def consistency_check(conn, name, errored_ids=None, hq_conn=None):
    """Compare `name`'s is_active/is_deleted flags against HQ users.

    Returns (flagged, missing_in_user): flags that disagree, and ids not in HQ
    users at all. `errored_ids` (sanity violations) are skipped.
    """
    errored_ids = set(errored_ids or [])
    owns_hq_conn = hq_conn is None
    if owns_hq_conn:
        hq_conn = get_hq_connection()

    try:
        cursor = conn.cursor()
        table_cols = [r["name"] for r in cursor.execute(f"PRAGMA table_info({name})")]
        if not table_cols or "is_active" not in table_cols or "is_deleted" not in table_cols:
            return [], []  # table missing here, or no flags to compare
        id_column = "user_id" if "user_id" in table_cols else "id"

        rows = cursor.execute(
            f"SELECT {id_column} AS id, is_active, is_deleted FROM {name}"
        ).fetchall()
        local = {
            row["id"]: (row["is_active"], row["is_deleted"])
            for row in rows
            if row["id"] not in errored_ids
        }

        hq_cursor = hq_conn.cursor()
        hq_flags = {}
        ids = list(local.keys())
        for i in range(0, len(ids), BATCH_SIZE):
            batch = ids[i : i + BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)
            hq_rows = hq_cursor.execute(
                f"SELECT id, is_active, is_deleted FROM users WHERE id IN ({placeholders})",
                batch,
            ).fetchall()
            for row in hq_rows:
                hq_flags[row["id"]] = (row["is_active"], row["is_deleted"])

        flagged = []
        missing_in_user = []
        for local_id, flags in local.items():
            if local_id not in hq_flags:
                missing_in_user.append(local_id)
            elif flags != hq_flags[local_id]:
                flagged.append(local_id)

        return flagged, missing_in_user
    finally:
        if owns_hq_conn:
            hq_conn.close()


def consistency_check_and_add_df(conn, name, dataframe, db_label, errored_ids=None, hq_conn=None):
    """Run consistency_check and write its two result columns:
    `{db_label}_{name}` (flag mismatches) and
    `{db_label}_{name}_missing_in_user` (ids missing from HQ users).
    """
    flagged, missing_in_user = consistency_check(
        conn, name, errored_ids=errored_ids, hq_conn=hq_conn
    )
    dataframe = add_to_df(dataframe, f"{db_label}_{name}", flagged)
    dataframe = add_to_df(dataframe, f"{db_label}_{name}_missing_in_user", missing_in_user)
    return dataframe

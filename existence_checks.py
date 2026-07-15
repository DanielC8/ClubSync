"""Cross-tier and within-tier existence checks."""

from db_connection import connect_to
from utils import add_to_df

BATCH_SIZE = 500

# member table + child tier for each level (hq -> regions -> clubs)
MODES = {
    "hq": {
        "member_table": "members",
        "child_member_table": "members",
        "child_filter": "region_id",
        "child_key": "region_id",
        "child_prefix": "region",
    },
    "region": {
        "member_table": "members",
        "child_member_table": "member_details",
        "child_filter": "club_id",
        "child_key": "club_id",
        "child_prefix": "club",
    },
    "club": {
        "member_table": "member_details",
        "child_member_table": None,
        "child_filter": None,
        "child_key": None,
        "child_prefix": None,
    },
}


def _columns(cursor, table):
    return [row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")]


def _id_column(cols):
    return "user_id" if "user_id" in cols else "id"


def check_missing_ids(source_cursor, source_table, target_cursor, target_table,
                      dataframe, header, id_col="user_id", filter_col=None,
                      filter_val=None):
    """Report source_table ids that don't exist in target_table.

    filter_col/filter_val scope the source to one region/club so we don't compare a
    club against every other club's members.
    """
    source_cols = _columns(source_cursor, source_table)
    target_cols = _columns(target_cursor, target_table)

    if not source_cols or not target_cols:
        return add_to_df(dataframe, header, [])

    if id_col not in source_cols:
        id_col = _id_column(source_cols)

    sql = f"SELECT {id_col} AS id FROM {source_table}"
    params = []
    if filter_col and filter_col in source_cols and filter_val is not None:
        sql += f" WHERE {filter_col} = ?"
        params.append(filter_val)

    ids = [row["id"] for row in source_cursor.execute(sql, params) if row["id"] is not None]

    target_id_col = _id_column(target_cols)
    found = set()
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        placeholders = ",".join("?" for _ in batch)
        rows = target_cursor.execute(
            f"SELECT {target_id_col} AS id FROM {target_table} "
            f"WHERE {target_id_col} IN ({placeholders})",
            batch,
        )
        found.update(row["id"] for row in rows)

    missing = [uid for uid in ids if uid not in found]
    return add_to_df(dataframe, header, missing)


def _users_without_record(conn, member_table, dataframe, header):
    """Users that aren't a member or a leader anywhere."""
    cursor = conn.cursor()
    if not _columns(cursor, "users"):
        return add_to_df(dataframe, header, [])

    linked = set()
    for table in (member_table, "leaders"):
        cols = _columns(cursor, table)
        if not cols:
            continue
        id_col = _id_column(cols)
        linked.update(
            row["id"]
            for row in cursor.execute(f"SELECT {id_col} AS id FROM {table}")
            if row["id"] is not None
        )

    user_ids = [row["id"] for row in cursor.execute("SELECT id FROM users")]
    return add_to_df(dataframe, header, [uid for uid in user_ids if uid not in linked])


def existence_check(conn, entities, output, name, mode="region"):
    """Existence checks for one DB. `name` labels the columns, `entities` are the
    registry rows for the tier below."""
    config = MODES[mode]
    cursor = conn.cursor()
    member_table = config["member_table"]

    # within this DB
    output = _users_without_record(
        conn, member_table, output, f"{name}_users_not_in_members_or_leaders"
    )
    for table in (member_table, "leaders"):
        output = check_missing_ids(
            cursor, table, cursor, "users", output, f"{name}_{table}_missing_in_users"
        )

    # across tiers (clubs have nothing below them)
    if config["child_member_table"] is None:
        return output

    for entity in entities or []:
        child_id = entity[config["child_key"]]
        child_label = f"{config['child_prefix']}_{child_id}"
        child_conn = connect_to(entity["db_path"])
        try:
            child_cursor = child_conn.cursor()
            output = check_missing_ids(
                cursor,
                member_table,
                child_cursor,
                config["child_member_table"],
                output,
                f"{name}_members_missing_in_{child_label}",
                filter_col=config["child_filter"],
                filter_val=child_id,
            )
            # HQ leaders only carry club_id, so leaders are compared region->club
            if mode == "region":
                output = check_missing_ids(
                    cursor,
                    "leaders",
                    child_cursor,
                    "leaders",
                    output,
                    f"{name}_leaders_missing_in_{child_label}",
                    filter_col="club_id",
                    filter_val=child_id,
                )
        finally:
            child_conn.close()

    return output

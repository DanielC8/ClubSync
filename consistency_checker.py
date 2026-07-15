"""Orchestrator plus the flagged_ids.csv column guide.

Each column is a list of flagged user ids; empty means the check found nothing.

  sanity_check                          HQ users with is_active=1 AND is_deleted=1
  <db>_<table>                          flags disagree with HQ users
  <db>_<table>_missing_in_user          id not in HQ users at all
  <db>_users_not_in_members_or_leaders  user isn't a member or a leader
  <db>_<table>_missing_in_users         member/leader points at a missing user
  <db>_members_missing_in_<child>       cross-tier gap (parent has it, child doesn't)
  <db>_leaders_missing_in_<child>       same, for leaders
"""

import os
from pathlib import Path

import pandas as pd

from consistency_checks import consistency_check_and_add_df
from db_connection import connect_to, get_hq_connection
from email_script import load_email_config, send_email
from existence_checks import existence_check
from utils import add_to_df, sanity_check

CSV_PATH = Path(__file__).parent / "flagged_ids.csv"

# tables with is_active/is_deleted flags to compare, per tier
HQ_TABLES = ["users", "members", "leaders"]
REGION_TABLES = ["users", "members", "leaders"]
CLUB_TABLES = ["users", "member_details", "leaders"]


def check_env_var(key):
    """Optional int from the environment. Empty/unset -> None, junk -> error."""
    value = os.getenv(key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise EnvironmentError(f"{key} must be an integer, got {value!r}")


def _regions(cursor, target_region_id=None):
    sql = (
        "SELECT region_id, region_db_name, db_path FROM db_registry "
        "WHERE region_db_name IS NOT NULL"
    )
    params = []
    if target_region_id is not None:
        sql += " AND region_id = ?"
        params.append(target_region_id)
    return [dict(row) for row in cursor.execute(sql, params)]


def _clubs(cursor, target_club_id=None, target_region_id=None):
    sql = (
        "SELECT region_id, club_id, club_db_name, db_path FROM db_registry "
        "WHERE club_db_name IS NOT NULL"
    )
    params = []
    if target_region_id is not None:
        sql += " AND region_id = ?"
        params.append(target_region_id)
    if target_club_id is not None:
        sql += " AND club_id = ?"
        params.append(target_club_id)
    return [dict(row) for row in cursor.execute(sql, params)]


def check_hq_db(conn, cursor, output, errored_ids):
    # HQ is the source of truth, so only existence checks + the hop to regions
    for table in HQ_TABLES:
        output = consistency_check_and_add_df(
            conn, table, output, "hq", errored_ids=errored_ids, hq_conn=conn
        )
    return existence_check(conn, _regions(cursor), output, "hq", mode="hq")


def check_regions(cursor, output, errored_ids, target_region_id=None, hq_conn=None):
    for region in _regions(cursor, target_region_id):
        label = f"region_{region['region_id']}"
        conn = connect_to(region["db_path"])
        try:
            for table in REGION_TABLES:
                output = consistency_check_and_add_df(
                    conn, table, output, label, errored_ids=errored_ids, hq_conn=hq_conn
                )
            clubs = _clubs(cursor, target_region_id=region["region_id"])
            output = existence_check(conn, clubs, output, label, mode="region")
        finally:
            conn.close()
    return output


def check_clubs(cursor, output, errored_ids, target_club_id=None,
                target_region_id=None, hq_conn=None):
    for club in _clubs(cursor, target_club_id, target_region_id):
        label = f"club_{club['club_id']}"
        conn = connect_to(club["db_path"])
        try:
            for table in CLUB_TABLES:
                output = consistency_check_and_add_df(
                    conn, table, output, label, errored_ids=errored_ids, hq_conn=hq_conn
                )
            output = existence_check(conn, [], output, label, mode="club")
        finally:
            conn.close()
    return output


def run_checks(target_region_id=None, target_club_id=None):
    """Run all checks and return the results frame. Called by the CLI and the web app."""
    if target_region_id is None:
        target_region_id = check_env_var("TARGET_REGION_ID")
    if target_club_id is None:
        target_club_id = check_env_var("TARGET_CLUB_ID")

    errored_ids = sanity_check()

    output = pd.DataFrame()
    output = add_to_df(output, "sanity_check", errored_ids)  # first column

    hq_conn = get_hq_connection()
    try:
        cursor = hq_conn.cursor()
        output = check_hq_db(hq_conn, cursor, output, errored_ids)
        output = check_regions(cursor, output, errored_ids, target_region_id, hq_conn)
        output = check_clubs(
            cursor, output, errored_ids, target_club_id, target_region_id, hq_conn
        )
    finally:
        hq_conn.close()

    return output


def _findings(dataframe):
    """Only the columns that caught something."""
    return {
        column: dataframe[column].dropna().astype(int).tolist()
        for column in dataframe.columns
        if dataframe[column].notna().any()
    }


def main():
    dataframe = run_checks()
    dataframe.to_csv(CSV_PATH, index=False)

    findings = _findings(dataframe)
    print(f"Wrote {CSV_PATH.name} — {len(dataframe.columns)} checks run, "
          f"{len(findings)} with findings.\n")
    for column, ids in findings.items():
        print(f"  {column}: {ids}")

    config = load_email_config()
    if not config.get("enabled"):
        return dataframe

    preview = send_email(
        subject=config.get("subject", ""),
        sender_email=config.get("sender_email"),
        smtp_host=config.get("smtp_host"),
        smtp_port=config.get("smtp_port"),
        smtp_username=config.get("smtp_username"),
        smtp_password=os.getenv("SMTP_PASSWORD"),
        recipient_email=config.get("recipient_email"),
        content=config.get("content", ""),
        file_name=str(CSV_PATH),
        cc=config.get("cc"),
        use_tls=config.get("use_tls", True),
        dry_run=config.get("dry_run", True),
    )

    print("\n--- email preview (dry run, nothing sent) ---")
    print(f"subject:    {preview['subject']}")
    print(f"to:         {preview['to']}")
    print(f"cc:         {', '.join(preview['cc']) or '-'}")
    print(f"attachment: {preview['attachment'] or '-'}")
    print(f"sent:       {preview['sent']}")
    if preview.get("error"):
        print(f"error:      {preview['error']}")
    print()
    print(preview["content"])

    return dataframe


if __name__ == "__main__":
    main()

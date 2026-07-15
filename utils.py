"""Shared helpers: sanity_check(), add_to_df()."""

import pandas as pd

from db_connection import get_hq_connection


def sanity_check():
    """Find HQ users in the impossible state is_active=1 AND is_deleted=1.

    Returns a list of user ids.
    """
    conn = get_hq_connection()
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT id FROM users WHERE is_active = 1 AND is_deleted = 1"
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def add_to_df(dataframe, header, data):
    """Append `data` as a sorted Int64 column named `header`.

    Columns are different lengths, so we concat rather than assign — that pads the
    short ones with NaN instead of misaligning what's already there.
    """
    series = pd.Series(sorted(data) if data else [], dtype="Int64", name=header)
    return pd.concat([dataframe, series], axis=1)

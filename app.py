"""Flask demo: add records, run checks, preview the report email."""

import os

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

import seed_data
from consistency_checker import CSV_PATH, run_checks
from db_connection import DATA_DIR, get_hq_connection
from email_script import load_email_config, send_email
from network_writes import add_leader, add_member, update_user_flags

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "clubsync-demo")  # only signs flash cookies

# total flags per run, kept in memory to draw the sparkline (cleared on reset)
RUN_HISTORY = []


def ensure_seeded():
    # build the DBs on first boot only — don't clobber records added via the UI.
    # reseeding is the Reset button's job.
    if not DATA_DIR.exists() or not any(DATA_DIR.glob("*.db")):
        seed_data.build_all(reset=True)


def list_clubs():
    conn = get_hq_connection()
    try:
        rows = conn.execute(
            "SELECT club_id, region_id FROM db_registry "
            "WHERE club_db_name IS NOT NULL ORDER BY club_id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _severity(column):
    # sanity > mismatch > missing
    if column == "sanity_check":
        return "sanity"
    if "missing" in column or "not_in" in column:
        return "missing"
    return "mismatch"


def summarise(dataframe):
    """Structure the hit columns for the template — a clean run has ~60 empty ones
    the page skips. Each column carries its severity so cells can be coloured."""
    hits = [c for c in dataframe.columns if dataframe[c].notna().any()]

    columns = []
    n_rows = 0
    flags = 0
    for c in hits:
        ids = [str(int(v)) for v in dataframe[c].dropna().tolist()]
        flags += len(ids)
        n_rows = max(n_rows, len(ids))
        columns.append({"header": c, "severity": _severity(c), "ids": ids})
    for col in columns:
        col["cells"] = col["ids"] + [""] * (n_rows - len(col["ids"]))

    return {
        "total_checks": len(dataframe.columns),
        "found": len(hits),
        "flags": flags,
        "columns": columns,
        "n_rows": n_rows,
    }


def sparkline_svg(history, width=240, height=44):
    """A tiny inline-SVG line of the flag count across this session's runs."""
    if not history:
        return ""
    pad = 6
    lo, hi = min(history), max(history)
    span = (hi - lo) or 1
    step = (width - 2 * pad) / max(len(history) - 1, 1)

    def x(i):
        return pad + step * i

    def y(v):
        return height - pad - (height - 2 * pad) * ((v - lo) / span)

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(history))
    lx, ly = x(len(history) - 1), y(history[-1])
    return Markup(
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'class="spark" preserveAspectRatio="none">'
        f'<polyline points="{points}" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="currentColor"/></svg>'
    )


def email_preview():
    # dry_run pinned on — the web app never actually sends
    config = load_email_config()
    if not config.get("enabled"):
        return None
    return send_email(
        subject=config.get("subject", ""),
        sender_email=config.get("sender_email"),
        smtp_host=config.get("smtp_host"),
        smtp_port=config.get("smtp_port"),
        smtp_username=config.get("smtp_username"),
        smtp_password=None,
        recipient_email=config.get("recipient_email"),
        content=config.get("content", ""),
        file_name=str(CSV_PATH),
        cc=config.get("cc"),
        use_tls=config.get("use_tls", True),
        dry_run=True,
    )


@app.route("/")
def index():
    return render_template("index.html", clubs=list_clubs())


@app.route("/run", methods=["POST"])
def run():
    dataframe = run_checks()
    dataframe.to_csv(CSV_PATH, index=False)  # write before the preview so it can attach
    summary = summarise(dataframe)
    RUN_HISTORY.append(summary["flags"])
    return render_template(
        "index.html",
        clubs=list_clubs(),
        results=summary,
        preview=email_preview(),
        history=RUN_HISTORY,
        sparkline=sparkline_svg(RUN_HISTORY),
    )


@app.route("/add", methods=["POST"])
def add():
    name = (request.form.get("name") or "").strip()
    role = request.form.get("role", "member")
    skip_club = request.form.get("skip_club") == "1"

    if not name:
        flash("Give the person a name first.", "error")
        return redirect(url_for("index"))

    try:
        club_id = int(request.form["club_id"])
    except (KeyError, ValueError):
        flash("Pick a club.", "error")
        return redirect(url_for("index"))

    writer = add_leader if role == "leader" else add_member
    try:
        new_id, tiers = writer(name, club_id, propagate_to_club=not skip_club)
    except Exception as err:  # show it to the visitor instead of a 500
        flash(f"Couldn't add {name}: {err}", "error")
        return redirect(url_for("index"))

    where = ", ".join(tiers)
    if skip_club:
        flash(
            f"Added {role} {name} as id {new_id} to {where} — skipped the club tier, "
            f"so the next check should flag the gap.",
            "warn",
        )
    else:
        flash(f"Added {role} {name} as id {new_id} across {where}.", "ok")
    return redirect(url_for("index"))


@app.route("/flag_user", methods=["POST"])
def flag_user():
    try:
        user_id = int(request.form["user_id"])
    except (KeyError, ValueError):
        flash("Enter a numeric user id.", "error")
        return redirect(url_for("index"))

    action = request.form.get("action", "deactivate")
    is_active, is_deleted = (0, 1) if action == "delete" else (0, 0)
    try:
        update_user_flags(user_id, is_active, is_deleted)
    except Exception as err:  # show it instead of a 500
        flash(f"Couldn't update user {user_id}: {err}", "error")
        return redirect(url_for("index"))

    flash(
        f"Marked user {user_id} as {action}d in HQ — its region/club copies still "
        f"disagree, so the next check should flag a mismatch.",
        "warn",
    )
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    seed_data.build_all(reset=True)
    RUN_HISTORY.clear()
    flash("Demo data reset to the seeded network.", "ok")
    return redirect(url_for("index"))


@app.route("/flagged_ids.csv")
def download_csv():
    if not CSV_PATH.exists():
        abort(404)
    return send_file(CSV_PATH, as_attachment=True, download_name=CSV_PATH.name)


ensure_seeded()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

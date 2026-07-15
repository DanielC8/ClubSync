"""Flask demo: add records, run checks, preview the report email."""

import os

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for

import seed_data
from consistency_checker import CSV_PATH, run_checks
from db_connection import DATA_DIR, get_hq_connection
from email_script import load_email_config, send_email
from network_writes import add_leader, add_member

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "clubsync-demo")  # only signs flash cookies


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


def summarise(dataframe):
    """Just the columns with hits — a clean run has ~60 empty ones the page skips."""
    hits = [column for column in dataframe.columns if dataframe[column].notna().any()]
    return {
        "total_checks": len(dataframe.columns),
        "found": len(hits),
        "table": (
            dataframe[hits].to_html(index=False, na_rep="", border=0, classes="results")
            if hits
            else None
        ),
    }


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
    return render_template(
        "index.html",
        clubs=list_clubs(),
        results=summarise(dataframe),
        preview=email_preview(),
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


@app.route("/reset", methods=["POST"])
def reset():
    seed_data.build_all(reset=True)
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

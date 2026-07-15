# ClubSync

A multi-tier database consistency checker with an email-reporting pipeline and a
one-click web demo. It audits a club network spread across three SQLite tiers —
**HQ** (source of truth), **regions**, and **clubs** — and flags records that have
drifted out of sync.

The demo runs with **no secrets and no external services**, so a public link works
on every click.

## What it checks

- **Sanity** — HQ users stuck in the impossible `is_active=1 AND is_deleted=1` state.
- **Consistency** — a tier's `is_active`/`is_deleted` flags disagreeing with HQ, or
  pointing at a user HQ has never heard of.
- **Existence** — users who are neither a member nor a leader, member/leader rows
  pointing at a missing user, and records that don't line up across tiers.

Results land in one table, get written to `flagged_ids.csv`, and are summarised in
a report email (previewed in dry-run mode — nothing is actually sent from the demo).

## Run it locally

```bash
pip install -r requirements.txt
python seed_data.py            # builds data/*.db with a few planted inconsistencies
python consistency_checker.py  # writes flagged_ids.csv, prints the email preview
python app.py                  # web demo at http://localhost:8080
```

Try the demo: click **Run Consistency Check** to see the planted findings, add a
member with **"skip club tier"** ticked, then run again and watch a fresh cross-tier
gap show up. **Reset demo data** puts everything back.

## Configuration

Everything is optional — with nothing set, ClubSync checks the whole network and
previews the email.

| var                | purpose                                  | default |
|--------------------|------------------------------------------|---------|
| `SMTP_PASSWORD`    | real send (dry-run ignores it)           | unset   |
| `TARGET_REGION_ID` | scope checks to one region               | all     |
| `TARGET_CLUB_ID`   | scope checks to one club                 | all     |
| `PORT`             | web server port                          | 8080    |

Email settings live in `config.yaml`. The SMTP password is **never** stored there —
it's read from `$SMTP_PASSWORD` at send time, and `dry_run: true` keeps the demo
from sending anything.

## Deploy

The app binds `0.0.0.0` on `$PORT` and ships a `Procfile` (`web: python app.py`),
so it drops straight onto Render or Replit. See `ROADMAP.md` / the build spec for
the full deploy walkthrough.

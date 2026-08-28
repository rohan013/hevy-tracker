# Hevy local tracker

Syncs your [Hevy](https://www.hevy.com/) workout history into a local SQLite database and
keeps your training program in a git-tracked JSON file that can be pushed back to Hevy.
There is no web app and no server: the data lands on your machine, and you (or an agent
like Claude Code, reading the database in conversation) query it directly.

Two things it gives you that the Hevy app does not:

- **A queryable history.** One row per set, joined to exercise and workout, with a
  computed estimated 1RM. Any question you can write as SQL is answerable.
- **A version-controlled program.** Your routines live in `personal/program.json` under
  their own git history, so every change to your training is a commit with a reason
  attached, and the routines Hevy serves can be rebuilt from the file at any time.

Requires Python 3.9+, `requests`, and a Hevy API key (which needs Hevy Pro).

## Setup

```bash
git clone https://github.com/rohan013/hevy-tracker.git
cd hevy-tracker
pip3 install --user -r requirements.txt

cp .env.example .env          # then paste your key from hevy.com/settings?developer
python3 cli.py sync           # backfills your full history; safe to re-run if interrupted
python3 cli.py init           # builds personal/program.json from your existing routines
```

`init` reads the routines already in your account and writes them out in the compact form
`program.json` uses, then starts a git repo in `personal/` and commits the result. Open the
file, adjust it, and preview a push:

```bash
python3 cli.py push-routines --dry-run   # prints a before/after diff, writes nothing
python3 cli.py push-routines             # overwrites those routines in Hevy
```

If you have no routines worth importing, copy `program.example.json` to
`personal/program.json` and edit it instead. You will need your routine ids; after a sync
they are in the database:

```bash
sqlite3 data/hevy.db "SELECT id, title FROM routines"
```

## Your data stays yours

Everything specific to your account lives in `personal/`, which this repo ignores:

```
personal/
  program.json                  your routines, the source of truth for pushes
  routines/                     a snapshot of the live routines before every push
data/hevy.db                    your synced workout history (ignored)
.env                            your API key (ignored)
```

`init` makes `personal/` its own git repo. Nothing in it is ever committed to this one, so
you can push it to a private remote and get an auditable history of your program without
publishing your training data. Set `HEVY_PERSONAL_DIR` to keep it outside the checkout
entirely.

## Commands

- `python3 cli.py sync` — workouts, exercise templates, routines and body measurements.
  Incremental after the first run, via Hevy's events feed.
- `python3 cli.py sync --full` — force a full re-backfill of workouts.
- `python3 cli.py init` — build `personal/program.json` from your Hevy routines.
  `--force` rebuilds an existing one.
- `python3 cli.py push-routines` — overwrite the routines named in `program.json`.
  `--dry-run` shows the diff, `--day 01` limits it to one routine.
- `python3 cli.py status` — local row counts and sync state, cross-checked against Hevy's
  own workout count.

Every push first snapshots the live routines to `personal/routines/`. Keep those:
routine contents cannot be recovered from the workout log, which records only what was
performed. Restoring one is a `PUT` of an old payload.

## The program file

One entry per exercise. Weights are in pounds and converted on push.

```jsonc
{
  "routine_id": "...",        // the Hevy routine this day overwrites
  "title": "01",              // routine name in Hevy
  "focus": "Chest + Triceps", // written to the routine's notes field
  "exercises": [
    {
      "exercise_template_id": "50DFDFAB",   // Hevy's catalog id, from exercise_templates
      "title": "Incline Bench Press (Barbell)",
      "superset": "A",          // optional; same letter = same superset, per day
      "rest_seconds": 120,
      "warmup": { "weight_lb": 45, "reps": 10 },   // optional, one set
      "sets": 4,                // working sets, all rendered at the bottom of the range
      "rep_range": [6, 10],
      "weight_lb": 95,          // null means "find it in the gym"
      "notes": "..."            // optional; replaces the generated progression note
    }
  ]
}
```

Without a `notes` value, each exercise gets the double-progression rule written out: hit
the top of the rep range on every working set, then add the smallest increment available
and restart at the bottom.

Two things `init` cannot recover, because Hevy does not return them or the format cannot
hold them:

- **Exercises measured by duration or distance** (planks, cardio) are skipped, and `init`
  names each one it skipped. They are not in `program.json`, so a later push removes them
  from that routine.
- **A routine's own notes.** The list endpoint omits them, so `focus` starts empty and the
  first push replaces whatever the routine had.

Routines edited in the Hevy app are silently overwritten by the next push. If you change
something at the gym, fold it back into `program.json` — or re-run `init --force`, which
rebuilds the file from what Hevy currently holds.

## Querying

`lib/schema.sql` has the full schema. Two views do the tedious parts:

- `set_metrics` — one row per set, joined to exercise, workout and muscle group, with
  `est_1rm` (Epley, excluding warmup and dropset sets).
- `planned_vs_performed` — one row per planned exercise against what was actually logged,
  plus rows for anything performed that the routine did not call for. Only meaningful for
  workouts logged since the routine last changed; scope your queries by date.

```bash
sqlite3 data/hevy.db "SELECT workout_date, weight_kg, reps, est_1rm FROM set_metrics
                      WHERE exercise_title LIKE '%Bench Press%'
                      ORDER BY workout_date DESC LIMIT 10"
```

Weights are stored in kilograms, matching Hevy's API; multiply by 2.20462 for pounds.

There are no built-in analytics commands, and that is deliberate — see `CLAUDE.md`.

## Notes

This project is not affiliated with Hevy. It uses their public developer API, which is
rate limited; avoid tight loops of API-backed commands.

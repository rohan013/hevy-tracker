# Hevy local tracker — agent notes

This syncs the user's Hevy workout data into a local SQLite database (`data/hevy.db`) so
an agent can answer training questions and give feedback directly in conversation.

## Workflow

1. On the first workout-related question in a conversation, run `python3 cli.py sync`
   before answering (cheap after the first run — incremental via Hevy's events feed).
   Don't re-run it before every follow-up query in the same conversation; only re-sync
   mid-conversation if the user indicates something new was just logged in Hevy.
2. Answer questions by querying `data/hevy.db` directly with `sqlite3` — there is no
   fixed set of analytics commands. Use the `set_metrics` view (see `lib/schema.sql`)
   for exercise-level questions; it already handles the estimated-1RM formula (Epley,
   warmup/dropset sets excluded) and the exercises/workouts/templates join, so don't
   re-derive those by hand.
3. Write the actual feedback/analysis in the conversation, not in the scripts. Nothing
   in this project should generate prose or call an LLM — that would duplicate what the
   agent is already doing in chat. If you're ever tempted to add a "summary" or
   "insight" string to a script's output, don't — do that reasoning in conversation.
4. `sync`, `init` and `push-routines` need `HEVY_API_KEY` (from `.env`) and network
   access. Everything else (`status`, direct `sqlite3` queries) works fully offline.
5. Check-ins tend to be on demand, so a training question is often the first sign that
   several sessions have been logged. Check `planned_vs_performed` on any check-in that
   covers new workouts, not only when adherence is asked about directly.

## The program

`personal/program.json` is the source of truth for the user's Hevy routines. Edit it,
then run `python3 cli.py push-routines` to overwrite the routines in Hevy. If the file
does not exist yet, `python3 cli.py init` builds it from the routines already in the
account.

- Always run `push-routines --dry-run` first and read the diff. The command overwrites
  routines in a live account.
- Every push snapshots the current routines to `personal/routines/routines_backup_<ts>.json`
  before writing. Routine contents are not recoverable from the workout log, which records
  only what was performed. Restoring is a `PUT` of an old payload.
- `personal/` is a separate git repo, ignored by this one. A program change is not
  finished until it is both pushed and committed there, so its history stays aligned with
  what Hevy actually served. Commit messages should name the training change ("raise Day 3
  lateral raises 5→6 sets"), not the diff.
- Routines edited directly in the Hevy app are silently overwritten by the next push. If
  the user changes something at the gym, fold it into `program.json`.
- `push-routines` uses `PUT`, so routine ids survive and already-logged workouts stay
  linked to the routine they were performed under.
- If `personal/` holds notes on why the program looks the way it does, read them before
  proposing program changes.

## Useful facts

- Weights are stored in kilograms (`weight_kg`), matching Hevy's API. If the user trains
  in pounds, every stored value is a converted lb figure — report weights in lbs
  (`weight_kg * 2.20462`) unless asked otherwise. `program.json` is written in lbs and
  converted on push.
- `set_metrics.est_1rm` is NULL for warmup/dropset sets and for sets missing weight
  or reps — by design, don't backfill those.
- Match exercises via `exercise_templates.title` (fuzzy `LIKE`) before filtering
  `set_metrics` by `exercise_template_id` — users refer to exercises by partial or casual
  names (e.g. "bench" for "Bench Press (Barbell)").
- SQLite's `datetime()` has no `weeks` modifier — `datetime('now', '-8 weeks')`
  silently returns an empty string (not an error), so a `WHERE` clause built on it
  quietly matches zero rows instead of failing loudly. Use days (weeks × 7) or
  months/years instead.
- `start_time`/`workout_date` are stored like `2026-07-03T16:28:21+00:00`, while
  `datetime('now', ...)` outputs a space-separated, offset-less string. Comparing
  these as raw strings mostly works (date prefixes still sort correctly) but isn't
  reliable right at a day boundary — wrap both sides in `datetime(...)` or use
  `julianday()` diffs when a query's correctness depends on a precise cutoff.
- Timestamps are stored in UTC, and evening sessions start after midnight UTC, so their
  UTC date is a day ahead of the day they were trained — a 20:25 local session on Tuesday
  is stored as Wednesday. Convert with `datetime(start_time, 'localtime')` when reporting
  a date or day of week, and when bucketing sessions into weeks. Never use a fixed offset:
  a `-7 hours` that is right in summer puts winter sessions an hour late.
- `planned_vs_performed` joins the routines against logged workouts: one row per planned
  exercise, plus rows for anything performed that the routine did not call for
  (`planned_idx IS NULL`). A planned row with `performed_sets = 0` is a skipped exercise.
  It is only meaningful for workouts logged *since the routine last changed* — routines
  store their current contents only, so older workouts get compared against a template
  they were never performed under and show spurious skips. Scope queries by date.
- Assisted exercises invert: for `Pull Up (Assisted)`, the logged weight is the assistance,
  so falling weight is progress. Use `MIN(weight_kg)` to track it, never `est_1rm`, which
  reads the assistance as load and reports improvement as decline.
- The Hevy API rate limits aggressively and returns 429 with an empty body. The client
  backs off on 429/503 (`config.RATE_LIMIT_BACKOFF_SECONDS`); avoid tight loops of
  API-backed commands regardless.

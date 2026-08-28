#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import config  # noqa: E402
import db as dbmod  # noqa: E402
import program as progmod  # noqa: E402
import program_import as importmod  # noqa: E402
import sync as syncmod  # noqa: E402
from hevy_client import HevyClient, HevyAPIError  # noqa: E402

TABLES = [
    "workouts", "exercises", "sets", "exercise_templates", "body_measurements",
    "routines", "routine_exercises", "routine_sets",
]


def _display_path(path):
    """Relative while the personal directory sits inside the checkout, absolute once
    HEVY_PERSONAL_DIR moves it out -- a path of ../../.. helps nobody."""
    relative = os.path.relpath(path, config.PROJECT_ROOT)
    return str(path) if relative.startswith("..") else relative


def _init_personal_repo():
    """The personal directory is ignored by this repo, so its history belongs to whoever
    set it up. Starting that history here is what makes program changes auditable by
    default rather than by remembering to."""
    if (config.PERSONAL_DIR / ".git").exists():
        return "already a git repo, left as it is"
    if not shutil.which("git"):
        return "git not installed, skipped"
    try:
        for command in (
            ["git", "init", "-q"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "Import program from Hevy"],
        ):
            subprocess.run(command, cwd=config.PERSONAL_DIR, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode().strip()
        if "auto-detect email" in stderr or "tell me who you are" in stderr.lower():
            return (
                "initialised, but nothing committed yet: git has no identity here. Set one "
                f"with 'git -C {_display_path(config.PERSONAL_DIR)} config user.email you@example.com' "
                "and commit."
            )
        detail = stderr.splitlines()
        return f"git setup failed: {detail[-1] if detail else exc}"
    return "initialised, program committed"


def _fetch_routines(client):
    routines = []
    for _page, _page_count, items in client.paginate(
        client.list_routines_page, "routines", page_size=10
    ):
        routines.extend(items)
    return routines


def cmd_init(args):
    """Writes the personal directory this tool reads: a program.json built from the
    routines already in the account, under its own git history."""
    api_key = config.get_api_key()
    if not api_key:
        return {"error": "HEVY_API_KEY not set. Add it to .env (see .env.example)."}, 1
    if config.PROGRAM_PATH.exists() and not args.force:
        return {
            "error": f"{config.PROGRAM_PATH} already exists. Pass --force to rebuild it "
                     "from Hevy (its git history keeps the old version)."
        }, 1

    routines = _fetch_routines(HevyClient(api_key))
    if not routines:
        return {
            "error": "no routines found in this Hevy account. Build them in the app first, "
                     "or copy program.example.json to " + str(config.PROGRAM_PATH) + "."
        }, 1

    document, warnings = importmod.build_program(routines)
    config.ROUTINE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    config.PROGRAM_PATH.write_text(json.dumps(document, indent=2) + "\n")

    for day in document["days"]:
        print(f"\n=== {day['title']} ===")
        for line in progmod.describe_day(day):
            print(f"    {line}")
    if warnings:
        print("\nwarnings:")
        for warning in warnings:
            print(f"  - {warning}")
    print()

    return {
        "program_path": _display_path(config.PROGRAM_PATH),
        "routines_imported": [day["title"] for day in document["days"]],
        "warnings": warnings,
        "git": _init_personal_repo(),
        "next": "edit the program, then run: python3 cli.py push-routines --dry-run",
    }, 0


def cmd_sync(args):
    api_key = config.get_api_key()
    if not api_key:
        return {"error": "HEVY_API_KEY not set. Add it to .env (see .env.example)."}, 1
    try:
        return syncmod.run_sync(config.DB_PATH, api_key, force_full=args.full), 0
    except HevyAPIError as exc:
        return {"error": exc.message, "status_code": exc.status_code}, 1


def cmd_status(args):
    conn = dbmod.init_db(config.DB_PATH)
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
    result = {
        "counts": counts,
        "backfill_complete": dbmod.get_state(conn, "workouts_backfill_complete", "0") == "1",
        "last_sync_at": dbmod.get_state(conn, "last_sync_at"),
    }
    api_key = config.get_api_key()
    if api_key:
        try:
            remote_count = HevyClient(api_key).get_workout_count()
            result["remote_workout_count"] = remote_count
            result["counts_match"] = remote_count == counts["workouts"]
        except HevyAPIError as exc:
            result["remote_check_error"] = exc.message
    else:
        result["remote_check_error"] = "HEVY_API_KEY not set, skipped remote check"
    conn.close()
    return result, 0


def _describe_live(routine):
    """Same shape as program.describe_day, but read off what Hevy currently holds, so
    the two can be read side by side in the push diff."""
    if routine is None:
        return ["(not found in Hevy)"]
    lines = []
    for ex in routine.get("exercises") or []:
        work = [s for s in ex.get("sets") or [] if s.get("type") != "warmup"]
        reps = sorted({s["reps"] for s in work if s.get("reps")})
        span = f"{reps[0]}-{reps[-1]}" if len(reps) > 1 else (str(reps[0]) if reps else "-")
        marker = "S. " if ex.get("superset_id") is not None else "   "
        lines.append(
            f"{marker}{ex.get('title', '?'):<38} {len(work)} x {span}  {ex.get('rest_seconds')}s"
        )
    return lines


def cmd_push_routines(args):
    """Overwrites the routines named in program.json, in place. Always snapshots the
    current state to the personal directory first -- routine contents are not recoverable
    from the workout log, which records only what was performed."""
    api_key = config.get_api_key()
    if not api_key:
        return {"error": "HEVY_API_KEY not set. Add it to .env (see .env.example)."}, 1
    if not config.PROGRAM_PATH.exists():
        return {
            "error": f"no program at {config.PROGRAM_PATH}. Run 'python3 cli.py init' to "
                     "build one from the routines already in your Hevy account."
        }, 1

    client = HevyClient(api_key)
    days = progmod.load_program()["days"]
    if args.day:
        days = [d for d in days if d["title"] == args.day]
        if not days:
            return {"error": f"no day titled {args.day!r} in program.json"}, 1

    live = _fetch_routines(client)
    live_by_id = {r["id"]: r for r in live}

    result = {"dry_run": bool(args.dry_run), "days": []}

    if not args.dry_run:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        config.ROUTINE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = config.ROUTINE_BACKUP_DIR / f"routines_backup_{stamp}.json"
        backup.write_text(json.dumps({"fetched_at": stamp, "routines": live}, indent=2))
        result["backup"] = _display_path(backup)

    for day in days:
        print(f"\n=== {day['title']} -- {day.get('focus', '')} ===")
        print("  before:")
        for line in _describe_live(live_by_id.get(day["routine_id"])):
            print(f"    {line}")
        print("  after:")
        for line in progmod.describe_day(day):
            print(f"    {line}")

        entry = {"title": day["title"], "routine_id": day["routine_id"]}
        if not args.dry_run:
            client.update_routine(day["routine_id"], progmod.render_day(day))
            entry["pushed"] = True
            time.sleep(config.REQUEST_DELAY_SECONDS)
        result["days"].append(entry)

    print()
    return result, 0


def main():
    parser = argparse.ArgumentParser(description="Hevy local sync/status tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init", help="Create the personal directory and build program.json from your Hevy routines"
    )
    p_init.add_argument(
        "--force", action="store_true", help="Rebuild program.json even if one already exists"
    )
    p_init.set_defaults(func=cmd_init)

    p_sync = sub.add_parser("sync", help="Sync workouts, exercise templates, and body measurements from Hevy")
    p_sync.add_argument("--full", action="store_true", help="Force a full re-backfill of workouts")
    p_sync.set_defaults(func=cmd_sync)

    p_push = sub.add_parser(
        "push-routines", help="Overwrite the Hevy routines defined in program.json"
    )
    p_push.add_argument(
        "--dry-run", action="store_true", help="Show the diff without writing anything"
    )
    p_push.add_argument("--day", help="Push only this day (matches the title in program.json)")
    p_push.set_defaults(func=cmd_push_routines)

    p_status = sub.add_parser("status", help="Show local sync status, cross-checked against Hevy")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    result, exit_code = args.func(args)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

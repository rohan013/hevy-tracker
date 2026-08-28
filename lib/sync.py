import json
from datetime import datetime, timedelta, timezone

import config
import db as dbmod
from hevy_client import HevyClient


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _format_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shift_back(ts, seconds=1):
    """Nudge a cursor timestamp back slightly as a safety buffer against unknown
    inclusive/exclusive semantics on the `since` param. Idempotent upserts make any
    resulting overlap harmless. Falls back to the raw string if it doesn't parse."""
    try:
        return _format_iso(_parse_iso(ts) - timedelta(seconds=seconds))
    except ValueError:
        return ts


def sync_exercise_templates(conn, client):
    count = 0
    for _page, _page_count, items in client.paginate(
        client.list_exercise_templates_page, "exercise_templates", page_size=100
    ):
        for t in items:
            dbmod.upsert_exercise_template(
                conn,
                {
                    "id": t["id"],
                    "title": t.get("title"),
                    "type": t.get("type"),
                    "primary_muscle_group": t.get("primary_muscle_group"),
                    "secondary_muscle_groups": json.dumps(t.get("secondary_muscle_groups") or []),
                    "equipment_category": t.get("equipment_category"),
                    "is_custom": 1 if t.get("is_custom") else 0,
                },
            )
            count += 1
        conn.commit()
    return count


def sync_routines(conn, client):
    routines = []
    for _page, _page_count, items in client.paginate(
        client.list_routines_page, "routines", page_size=10
    ):
        routines.extend(items)
    dbmod.replace_routines(conn, routines)
    conn.commit()
    return len(routines)


def sync_body_measurements(conn, client):
    count = 0
    for _page, _page_count, items in client.paginate(
        client.list_body_measurements_page, "body_measurements", page_size=10
    ):
        for m in items:
            dbmod.upsert_body_measurement(conn, m)
            count += 1
        conn.commit()
    return count


def sync_workouts_backfill(conn, client):
    start_page = int(dbmod.get_state(conn, "workouts_backfill_next_page", 1))
    count = 0
    for page, _page_count, items in client.paginate(
        client.list_workouts_page, "workouts", page_size=10, start_page=start_page
    ):
        for w in items:
            dbmod.upsert_workout(conn, w)
            count += 1
        dbmod.set_state(conn, "workouts_backfill_next_page", page + 1)
        conn.commit()
    return count


def sync_workouts_incremental(conn, client, since):
    events = []
    page = 1
    while True:
        data = client.list_workout_events_page(since=since, page=page, page_size=10)
        events.extend(data.get("events", []))
        page_count = data.get("page_count", page)
        if page >= page_count:
            break
        page += 1

    updated, deleted = 0, 0
    max_ts = None
    for ev in events:
        ts = ev.get("workout", {}).get("updated_at") if ev.get("type") == "updated" else ev.get("deleted_at")
        if ts and (max_ts is None or ts > max_ts):
            max_ts = ts
        if ev.get("type") == "updated":
            dbmod.upsert_workout(conn, ev["workout"])
            updated += 1
        elif ev.get("type") == "deleted":
            dbmod.delete_workout(conn, ev["id"])
            deleted += 1
    conn.commit()
    return updated, deleted, max_ts


def run_sync(db_path, api_key, force_full=False):
    conn = dbmod.init_db(db_path)
    client = HevyClient(api_key)

    if force_full:
        dbmod.set_state(conn, "workouts_backfill_complete", "0")
        dbmod.set_state(conn, "workouts_backfill_next_page", 1)
        dbmod.set_state(conn, "events_since_cursor", "")
        conn.commit()

    result = {
        "templates_synced": sync_exercise_templates(conn, client),
        "measurements_synced": sync_body_measurements(conn, client),
        "routines_synced": sync_routines(conn, client),
    }

    backfill_complete = dbmod.get_state(conn, "workouts_backfill_complete", "0") == "1"

    if not backfill_complete:
        backfill_start_ts = _now_iso()
        workouts_synced = sync_workouts_backfill(conn, client)
        dbmod.set_state(conn, "workouts_backfill_complete", "1")
        dbmod.set_state(conn, "events_since_cursor", backfill_start_ts)
        conn.commit()
        result.update(
            {"mode": "backfill", "workouts_synced": workouts_synced, "backfill_complete": True}
        )
    else:
        since = dbmod.get_state(conn, "events_since_cursor", "1970-01-01T00:00:00Z")
        updated, deleted, max_ts = sync_workouts_incremental(conn, client, since)
        if max_ts:
            dbmod.set_state(conn, "events_since_cursor", _shift_back(max_ts))
            conn.commit()
        result.update(
            {
                "mode": "incremental",
                "workouts_updated": updated,
                "workouts_deleted": deleted,
                "backfill_complete": True,
            }
        )

    dbmod.set_state(conn, "last_sync_at", _now_iso())
    conn.commit()
    conn.close()
    return result

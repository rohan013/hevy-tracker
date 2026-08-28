import os
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

BODY_MEASUREMENT_FIELDS = [
    "date", "weight_kg", "lean_mass_kg", "fat_percent", "neck_cm", "shoulder_cm",
    "chest_cm", "left_bicep_cm", "right_bicep_cm", "left_forearm_cm", "right_forearm_cm",
    "abdomen", "waist", "hips", "left_thigh", "right_thigh", "left_calf", "right_calf",
]


def get_connection(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def upsert_workout(conn, workout):
    conn.execute(
        """
        INSERT INTO workouts (id, title, description, routine_id, start_time, end_time, updated_at, created_at)
        VALUES (:id, :title, :description, :routine_id, :start_time, :end_time, :updated_at, :created_at)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            routine_id = excluded.routine_id,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            updated_at = excluded.updated_at,
            created_at = excluded.created_at
        """,
        {
            "id": workout["id"],
            "title": workout.get("title"),
            "description": workout.get("description"),
            "routine_id": workout.get("routine_id"),
            "start_time": workout.get("start_time"),
            "end_time": workout.get("end_time"),
            "updated_at": workout.get("updated_at"),
            "created_at": workout.get("created_at"),
        },
    )

    # No stable ids on nested exercises/sets from the API, so replace wholesale
    # rather than trying to diff.
    conn.execute("DELETE FROM exercises WHERE workout_id = ?", (workout["id"],))
    for ex in workout.get("exercises") or []:
        cur = conn.execute(
            """
            INSERT INTO exercises (workout_id, idx, title, notes, exercise_template_id, superset_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workout["id"],
                ex.get("index"),
                ex.get("title"),
                ex.get("notes"),
                ex.get("exercise_template_id"),
                ex.get("supersets_id"),
            ),
        )
        exercise_id = cur.lastrowid
        for st in ex.get("sets") or []:
            conn.execute(
                """
                INSERT INTO sets (exercise_id, idx, type, weight_kg, reps, distance_meters, duration_seconds, rpe, custom_metric)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exercise_id,
                    st.get("index"),
                    st.get("type"),
                    st.get("weight_kg"),
                    st.get("reps"),
                    st.get("distance_meters"),
                    st.get("duration_seconds"),
                    st.get("rpe"),
                    st.get("custom_metric"),
                ),
            )


def delete_workout(conn, workout_id):
    conn.execute("DELETE FROM workouts WHERE id = ?", (workout_id,))


def upsert_exercise_template(conn, template):
    conn.execute(
        """
        INSERT INTO exercise_templates (id, title, type, primary_muscle_group, secondary_muscle_groups, equipment_category, is_custom)
        VALUES (:id, :title, :type, :primary_muscle_group, :secondary_muscle_groups, :equipment_category, :is_custom)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            type = excluded.type,
            primary_muscle_group = excluded.primary_muscle_group,
            secondary_muscle_groups = excluded.secondary_muscle_groups,
            equipment_category = excluded.equipment_category,
            is_custom = excluded.is_custom
        """,
        template,
    )


def upsert_body_measurement(conn, measurement):
    row = {field: measurement.get(field) for field in BODY_MEASUREMENT_FIELDS}
    cols = ", ".join(BODY_MEASUREMENT_FIELDS)
    placeholders = ", ".join(f":{f}" for f in BODY_MEASUREMENT_FIELDS)
    updates = ", ".join(f"{f} = excluded.{f}" for f in BODY_MEASUREMENT_FIELDS if f != "date")
    conn.execute(
        f"INSERT INTO body_measurements ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(date) DO UPDATE SET {updates}",
        row,
    )


def replace_routines(conn, routines):
    """Routines are few and change wholesale, so a full replace is simpler and cheaper
    than diffing. Child rows go via ON DELETE CASCADE, which needs
    PRAGMA foreign_keys = ON -- get_connection sets it."""
    conn.execute("DELETE FROM routines")
    for routine in routines:
        conn.execute(
            """
            INSERT INTO routines (id, title, folder_id, notes, updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                routine["id"],
                routine.get("title"),
                routine.get("folder_id"),
                routine.get("notes"),
                routine.get("updated_at"),
                routine.get("created_at"),
            ),
        )
        for ex in routine.get("exercises") or []:
            cur = conn.execute(
                """
                INSERT INTO routine_exercises
                    (routine_id, idx, title, notes, exercise_template_id, superset_id, rest_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    routine["id"],
                    ex.get("index"),
                    ex.get("title"),
                    ex.get("notes"),
                    ex.get("exercise_template_id"),
                    ex.get("superset_id"),
                    ex.get("rest_seconds"),
                ),
            )
            routine_exercise_id = cur.lastrowid
            for st in ex.get("sets") or []:
                conn.execute(
                    """
                    INSERT INTO routine_sets (routine_exercise_id, idx, type, weight_kg, reps)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        routine_exercise_id,
                        st.get("index"),
                        st.get("type"),
                        st.get("weight_kg"),
                        st.get("reps"),
                    ),
                )

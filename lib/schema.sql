-- Hevy local cache schema. Safe to run repeatedly (IF NOT EXISTS everywhere).
-- Callers must set PRAGMA foreign_keys = ON on each connection for cascades to fire.

CREATE TABLE IF NOT EXISTS workouts (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    routine_id TEXT,
    start_time TEXT,
    end_time TEXT,
    updated_at TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workouts_start_time ON workouts(start_time);

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id TEXT NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    idx INTEGER,
    title TEXT,
    notes TEXT,
    exercise_template_id TEXT,
    superset_id INTEGER,
    UNIQUE(workout_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_exercises_workout_id ON exercises(workout_id);
CREATE INDEX IF NOT EXISTS idx_exercises_template_id ON exercises(exercise_template_id);

CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    idx INTEGER,
    type TEXT,
    weight_kg REAL,
    reps INTEGER,
    distance_meters REAL,
    duration_seconds REAL,
    rpe REAL,
    custom_metric REAL,
    UNIQUE(exercise_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_sets_exercise_id ON sets(exercise_id);

CREATE TABLE IF NOT EXISTS exercise_templates (
    id TEXT PRIMARY KEY,
    title TEXT,
    type TEXT,
    primary_muscle_group TEXT,
    secondary_muscle_groups TEXT, -- JSON-encoded array
    equipment_category TEXT,
    is_custom INTEGER
);
CREATE INDEX IF NOT EXISTS idx_exercise_templates_title ON exercise_templates(title);

CREATE TABLE IF NOT EXISTS body_measurements (
    date TEXT PRIMARY KEY, -- YYYY-MM-DD
    weight_kg REAL,
    lean_mass_kg REAL,
    fat_percent REAL,
    neck_cm REAL,
    shoulder_cm REAL,
    chest_cm REAL,
    left_bicep_cm REAL,
    right_bicep_cm REAL,
    left_forearm_cm REAL,
    right_forearm_cm REAL,
    abdomen REAL,
    waist REAL,
    hips REAL,
    left_thigh REAL,
    right_thigh REAL,
    left_calf REAL,
    right_calf REAL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Flattened, denormalized view for ad-hoc analysis. One row per set, joined up to
-- workout and exercise template, with a computed estimated-1RM column (Epley formula).
-- This is the single source of truth for that formula and these joins so every
-- ad-hoc query, yours or an agent's, gets consistent numbers without re-deriving them.
DROP VIEW IF EXISTS set_metrics;
CREATE VIEW set_metrics AS
SELECT
    s.id AS set_id,
    w.id AS workout_id,
    w.title AS workout_title,
    w.start_time AS workout_date,
    e.title AS exercise_title,
    e.exercise_template_id AS exercise_template_id,
    et.primary_muscle_group AS primary_muscle_group,
    et.equipment_category AS equipment_category,
    s.idx AS set_index,
    s.type AS set_type,
    s.weight_kg AS weight_kg,
    s.reps AS reps,
    s.rpe AS rpe,
    s.distance_meters AS distance_meters,
    s.duration_seconds AS duration_seconds,
    CASE
        WHEN s.type IN ('normal', 'failure') AND s.weight_kg > 0 AND s.reps > 0
        THEN s.weight_kg * (1 + s.reps / 30.0)
        ELSE NULL
    END AS est_1rm
FROM sets s
JOIN exercises e ON e.id = s.exercise_id
JOIN workouts w ON w.id = e.workout_id
LEFT JOIN exercise_templates et ON et.id = e.exercise_template_id;

-- Routines are Hevy's workout templates: what is *planned*, as opposed to the
-- workouts/exercises/sets tables which record what was *performed*. Kept locally so
-- adherence is a query rather than a manual comparison against the app.
CREATE TABLE IF NOT EXISTS routines (
    id TEXT PRIMARY KEY,
    title TEXT,
    folder_id TEXT,
    notes TEXT,
    updated_at TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS routine_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
    idx INTEGER,
    title TEXT,
    notes TEXT,
    exercise_template_id TEXT,
    superset_id INTEGER,
    rest_seconds INTEGER,
    UNIQUE(routine_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_routine_exercises_routine_id ON routine_exercises(routine_id);

CREATE TABLE IF NOT EXISTS routine_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_exercise_id INTEGER NOT NULL REFERENCES routine_exercises(id) ON DELETE CASCADE,
    idx INTEGER,
    type TEXT,
    weight_kg REAL,
    reps INTEGER,
    UNIQUE(routine_exercise_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_routine_sets_exercise_id ON routine_sets(routine_exercise_id);

-- Adherence: one row per planned exercise per logged workout, plus rows for anything
-- performed that the routine did not call for (planned_idx IS NULL). A planned row
-- with performed_sets = 0 is an exercise that was skipped.
--
-- Only meaningful for workouts logged since the routine last changed. Routines hold
-- their current contents only, so older workouts are compared against a template they
-- were never performed under and will show spurious skips.
DROP VIEW IF EXISTS planned_vs_performed;
CREATE VIEW planned_vs_performed AS
SELECT
    w.id                     AS workout_id,
    date(w.start_time)       AS workout_date,
    r.title                  AS routine_title,
    re.idx                   AS planned_idx,
    re.title                 AS exercise_title,
    re.exercise_template_id  AS exercise_template_id,
    (SELECT COUNT(*) FROM routine_sets rs
      WHERE rs.routine_exercise_id = re.id AND rs.type IN ('normal', 'failure')) AS planned_sets,
    (SELECT COUNT(*) FROM sets s
      WHERE s.exercise_id = e.id AND s.type IN ('normal', 'failure'))            AS performed_sets
FROM workouts w
JOIN routines r           ON r.id = w.routine_id
JOIN routine_exercises re ON re.routine_id = r.id
LEFT JOIN exercises e     ON e.workout_id = w.id
                         AND e.exercise_template_id = re.exercise_template_id
UNION ALL
SELECT
    w.id,
    date(w.start_time),
    r.title,
    NULL,
    e.title,
    e.exercise_template_id,
    0,
    (SELECT COUNT(*) FROM sets s
      WHERE s.exercise_id = e.id AND s.type IN ('normal', 'failure'))
FROM workouts w
JOIN routines r     ON r.id = w.routine_id
JOIN exercises e    ON e.workout_id = w.id
WHERE NOT EXISTS (
    SELECT 1 FROM routine_exercises re
    WHERE re.routine_id = r.id
      AND re.exercise_template_id = e.exercise_template_id
);

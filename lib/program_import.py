"""Builds program.json from the routines an account already has in Hevy.

The inverse of program.render_day: it takes the per-set structure the API returns and
collapses it back into the compact, one-entry-per-exercise form program.json uses, so
setting up amounts to running `cli.py init` rather than hand-writing a file of routine
ids. The result is a starting point to edit, not a faithful round trip -- a routine whose
working sets all carry the same rep count cannot say what rep range it was meant to run
in, and a routine's own notes are not returned by the list endpoint at all.
"""

import re
import string

import program

PROGRAM_COMMENT = (
    "Source of truth for the Hevy routines listed here. Edit this file, then run "
    "'python3 cli.py push-routines'. Routines edited in the Hevy app directly are "
    "overwritten by the next push. weight_lb: null means the working weight has not "
    "been established yet."
)


GENERATED_NOTE = re.compile(
    r"^Double progression: (\d+) reps on all (\d+) sets -> add weight, restart at (\d+)\.$"
)


def _recover_rep_range(note, entry):
    """Rendered sets are all pinned to the bottom of the rep range, so the top is lost.
    A note this tool wrote states it; read it back rather than keep the note, and an
    import of a program this tool pushed comes back unchanged."""
    match = GENERATED_NOTE.match(note)
    if not match:
        return None
    high, sets, low = (int(group) for group in match.groups())
    return [low, high] if sets == entry["sets"] else None


def _round_lb(weight_kg):
    """Weights are entered in whole pounds and stored as kilograms, so converting back
    lands a hair off a round number. Snap to it."""
    pounds = program.kg_to_lb(weight_kg)
    if pounds is None:
        return None
    return round(pounds) if abs(pounds - round(pounds)) < 0.05 else round(pounds, 1)


def _superset_letters(exercises):
    """Hevy numbers supersets per routine; program.json labels them A, B, ..."""
    ids = dict.fromkeys(
        ex["superset_id"] for ex in exercises if ex.get("superset_id") is not None
    )
    return {superset_id: string.ascii_uppercase[i] for i, superset_id in enumerate(ids)}


def import_exercise(exercise, letters):
    """Returns (entry, warnings). entry is None when the exercise cannot be expressed."""
    warnings = []
    sets = exercise.get("sets") or []
    working = [s for s in sets if s.get("type") != "warmup"]
    title = exercise.get("title") or exercise.get("exercise_template_id")

    if not working:
        return None, [f"{title}: no working sets, skipped"]
    reps = [s.get("reps") for s in working]
    if any(count is None for count in reps):
        return None, [
            f"{title}: sets are measured by duration or distance, which program.json "
            "cannot express -- skipped, so the next push would drop it from the routine"
        ]

    entry = {
        "exercise_template_id": exercise["exercise_template_id"],
        "title": title,
    }
    letter = letters.get(exercise.get("superset_id"))
    if letter:
        entry["superset"] = letter
    entry["rest_seconds"] = exercise.get("rest_seconds") or 0

    warmups = [s for s in sets if s.get("type") == "warmup" and s.get("reps") is not None]
    if warmups:
        entry["warmup"] = {
            "weight_lb": _round_lb(warmups[0].get("weight_kg")),
            "reps": warmups[0]["reps"],
        }
    if len(warmups) > 1:
        warnings.append(f"{title}: {len(warmups)} warmup sets, kept the first")

    entry["sets"] = len(working)
    entry["rep_range"] = [min(reps), max(reps)]
    loaded = [s.get("weight_kg") for s in working if s.get("weight_kg") is not None]
    entry["weight_lb"] = _round_lb(max(loaded)) if loaded else None

    note = (exercise.get("notes") or "").strip()
    recovered = _recover_rep_range(note, entry)
    if recovered:
        entry["rep_range"] = recovered
    elif note and note != program.progression_note(entry):
        # Only carry a note that says something the progression rule would not.
        entry["notes"] = note
    return entry, warnings


def import_day(routine):
    """Returns (day, warnings). day is None when nothing in the routine could be read."""
    exercises = routine.get("exercises") or []
    letters = _superset_letters(exercises)
    title = routine.get("title") or routine["id"]
    entries, warnings = [], []

    for exercise in exercises:
        entry, exercise_warnings = import_exercise(exercise, letters)
        warnings.extend(f"{title}: {w}" for w in exercise_warnings)
        if entry is not None:
            entries.append(entry)

    if not entries:
        # Left out deliberately: a routine in the file is a routine push-routines
        # overwrites, and an empty day would overwrite it with nothing.
        return None, warnings + [f"{title}: nothing importable, left out of program.json"]

    day = {
        "routine_id": routine["id"],
        "title": title,
        "focus": "",
        "exercises": entries,
    }
    return day, warnings


def build_program(routines):
    days, warnings = [], []
    for routine in routines:
        day, day_warnings = import_day(routine)
        warnings.extend(day_warnings)
        if day is not None:
            days.append(day)
    uniform = sum(
        1 for day in days for e in day["exercises"] if e["rep_range"][0] == e["rep_range"][1]
    )
    if uniform:
        warnings.append(
            f"{uniform} exercise{'s' if uniform > 1 else ''} ha{'ve' if uniform > 1 else 's'} "
            "every working set at the same rep count, so the rep_range is [n, n] -- set the "
            "top of each range before pushing"
        )
    return {"units": "lb", "_comment": PROGRAM_COMMENT, "days": days}, warnings

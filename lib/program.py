"""Renders program.json into the payloads the Hevy routines API expects.

program.json is the source of truth for the four training routines. This module is the
only place that knows how to expand its compact form -- one entry per exercise, with a
set count and a rep range -- into the per-set structure the API wants. It performs no
network calls, so `push-routines --dry-run` shows exactly what a real push would send.
"""

import json
from pathlib import Path

import config

PROGRAM_PATH = config.PROGRAM_PATH
LB_PER_KG = 2.20462


def lb_to_kg(pounds):
    """Hevy stores kilograms; program.json is written in pounds because that is what
    the gym's plates and machines are labelled with."""
    return None if pounds is None else pounds / LB_PER_KG


def kg_to_lb(kilograms):
    return None if kilograms is None else kilograms * LB_PER_KG


def load_program(path=None):
    return json.loads(Path(path or PROGRAM_PATH).read_text())


def progression_note(exercise):
    """The double-progression rule, stated per exercise so it is readable mid-set.
    An explicit `notes` value wins -- assisted movements progress by reducing load
    and need the rule spelled out differently."""
    if exercise.get("notes"):
        return exercise["notes"]
    low, high = exercise["rep_range"]
    return (
        f"Double progression: {high} reps on all {exercise['sets']} sets "
        f"-> add weight, restart at {low}."
    )


def render_exercise(exercise, superset_ids):
    low, _high = exercise["rep_range"]
    weight_kg = lb_to_kg(exercise.get("weight_lb"))
    sets = []
    warmup = exercise.get("warmup")
    if warmup:
        sets.append(
            {
                "type": "warmup",
                "weight_kg": lb_to_kg(warmup["weight_lb"]),
                "reps": warmup["reps"],
            }
        )
    # Working sets start at the bottom of the rep range; double progression walks
    # them to the top before the load moves.
    sets.extend(
        {"type": "normal", "weight_kg": weight_kg, "reps": low}
        for _ in range(exercise["sets"])
    )
    return {
        "exercise_template_id": exercise["exercise_template_id"],
        "superset_id": superset_ids.get(exercise.get("superset")),
        "rest_seconds": exercise["rest_seconds"],
        "notes": progression_note(exercise),
        "sets": sets,
    }


def render_day(day):
    """Superset letters in program.json are per-day labels; Hevy wants integers."""
    letters = dict.fromkeys(e["superset"] for e in day["exercises"] if e.get("superset"))
    superset_ids = {letter: idx for idx, letter in enumerate(letters)}
    return {
        "title": day["title"],
        "notes": day.get("focus", ""),
        "exercises": [render_exercise(e, superset_ids) for e in day["exercises"]],
    }


def describe_day(day):
    """One line per exercise, for the push diff. Reads off program.json rather than
    the rendered payload so the exercise titles are available."""
    lines = []
    for ex in day["exercises"]:
        low, high = ex["rep_range"]
        weight = "find weight" if ex.get("weight_lb") is None else f"{ex['weight_lb']:g} lb"
        marker = f"{ex['superset']}. " if ex.get("superset") else "   "
        lines.append(
            f"{marker}{ex['title']:<38} {ex['sets']} x {low}-{high}  "
            f"{ex['rest_seconds']}s  {weight}"
        )
    return lines

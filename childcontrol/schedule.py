"""The weekly schedule: 7 days x 48 half-hour slots, one character per slot."""

from __future__ import annotations

from datetime import datetime, timedelta

FREE = "F"
STUDY = "S"
LOCKED = "L"

STATES = (FREE, STUDY, LOCKED)
STATE_NAMES = {FREE: "Free", STUDY: "Study", LOCKED: "Locked"}
STATE_COLORS = {FREE: "#2e7d32", STUDY: "#ef6c00", LOCKED: "#b71c1c"}
STATE_HELP = {
    FREE: "Everything allowed.",
    STUDY: "Games and distracting sites blocked, computer usable.",
    LOCKED: "Computer not usable at all.",
}

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SLOTS_PER_DAY = 48
SLOT_MINUTES = 24 * 60 // SLOTS_PER_DAY


def slot_index(moment: datetime) -> int:
    return (moment.hour * 60 + moment.minute) // SLOT_MINUTES


def slot_label(slot: int) -> str:
    minutes = slot * SLOT_MINUTES
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def blank_week(state: str = FREE) -> list[str]:
    return [state * SLOTS_PER_DAY for _ in DAYS]


def normalize(week) -> list[str]:
    """Repair a schedule read from disk so the rest of the code can trust it."""
    fixed = []
    for day in range(7):
        row = week[day] if isinstance(week, list) and day < len(week) else ""
        row = "".join(c if c in STATES else FREE for c in str(row))
        row = (row + FREE * SLOTS_PER_DAY)[:SLOTS_PER_DAY]
        fixed.append(row)
    return fixed


def state_at(week, moment: datetime) -> str:
    week = normalize(week)
    return week[moment.weekday()][slot_index(moment)]


def next_transition(week, moment: datetime) -> tuple[datetime, str] | None:
    """When the state next changes, and what it changes to."""
    week = normalize(week)
    current = state_at(week, moment)
    cursor = moment.replace(second=0, microsecond=0)
    cursor -= timedelta(minutes=cursor.minute % SLOT_MINUTES)
    for _ in range(SLOTS_PER_DAY * 8):
        cursor += timedelta(minutes=SLOT_MINUTES)
        state = week[cursor.weekday()][slot_index(cursor)]
        if state != current:
            return cursor, state
    return None


def next_free(week, moment: datetime) -> datetime | None:
    """The next moment the weekly schedule allows Free use, or None if it never does."""
    week = normalize(week)
    cursor = moment.replace(second=0, microsecond=0)
    cursor -= timedelta(minutes=cursor.minute % SLOT_MINUTES)
    for _ in range(SLOTS_PER_DAY * 8):
        cursor += timedelta(minutes=SLOT_MINUTES)
        if week[cursor.weekday()][slot_index(cursor)] == FREE:
            return cursor
    return None


def describe_relative(target: datetime, now: datetime) -> str:
    """'today at 19:00' / 'tomorrow at 8:00' / 'Monday at 8:00'."""
    if target.date() == now.date():
        return f"today at {target.strftime('%H:%M')}"
    if target.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {target.strftime('%H:%M')}"
    return f"{target.strftime('%A')} at {target.strftime('%H:%M')}"


def describe_day(row: str) -> str:
    """Human summary of one day, e.g. 'Free 00:00-16:00, Study 16:00-19:00'."""
    row = normalize([row] * 7)[0]
    parts, start = [], 0
    for slot in range(1, SLOTS_PER_DAY + 1):
        if slot == SLOTS_PER_DAY or row[slot] != row[start]:
            end = "24:00" if slot == SLOTS_PER_DAY else slot_label(slot)
            parts.append(f"{STATE_NAMES[row[start]]} {slot_label(start)}-{end}")
            start = slot
    return ", ".join(parts)

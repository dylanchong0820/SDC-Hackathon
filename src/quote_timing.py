"""Content-free wall-clock timings for comparison stages."""
import time


STAGES = ("queue", "reading", "connecting", "analyzing", "ranking")
TERMINAL = {"completed", "failed", "cancelled"}


def advance_timings(previous, stage, now=None):
    """Close the previous stage and open the next; never include source data."""
    now = time.time() if now is None else now
    timings = {key: dict(value) for key, value in previous.items() if key in STAGES}
    for key, value in timings.items():
        if key != stage and "started_at" in value:
            value["seconds"] = round(
                value.get("seconds", 0) + max(0, now - value.pop("started_at")), 3)
    if stage in STAGES:
        value = timings.setdefault(stage, {"seconds": 0})
        value.setdefault("started_at", now)
    return timings

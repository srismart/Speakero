"""Deterministic delivery scoring from detector stats.

Zero LLM tokens: the same speech always produces the same score. The
content score lives in report.py (LLM-judged); this module only covers
delivery mechanics (fillers, pace, pauses).
"""
from typing import Optional

MIN_WORDS = 20  # below this the sample is too small to score fairly

PACE_LOW = 110
PACE_HIGH = 160
PAUSES_PER_MIN_OK = 4

FILLER_PENALTY_CAP = 30
PACE_PENALTY_CAP = 25
PAUSE_PENALTY_CAP = 15


def compute_delivery(filler_count: int, total_words: int, wpm: int,
                     pause_count: int, duration_seconds: float) -> Optional[dict]:
    """Return {"score": 0-100, "drivers": [{"label", "delta"}]} or None.

    Positive drivers carry fixed display bonuses (+6/+6/+4); they are not
    added to the score, which starts at 100 and only loses points.
    """
    if total_words < MIN_WORDS:
        return None

    score = 100
    drivers = []

    filler_rate = (filler_count / total_words) * 100
    penalty = min(FILLER_PENALTY_CAP, round(filler_rate * 6))
    if penalty > 0:
        score -= penalty
        drivers.append({"label": f"filler rate {filler_rate:.1f}%", "delta": -penalty})
    else:
        drivers.append({"label": "low filler usage", "delta": 6})

    if wpm < PACE_LOW:
        distance = PACE_LOW - wpm
        penalty = min(PACE_PENALTY_CAP, round(distance * 0.5))
    elif wpm > PACE_HIGH:
        distance = wpm - PACE_HIGH
        penalty = min(PACE_PENALTY_CAP, round(distance * 0.5))
    else:
        penalty = 0
    if penalty > 0:
        score -= penalty
        kind = "slow" if wpm < PACE_LOW else "rushed"
        drivers.append({"label": f"{kind} pace ({wpm} wpm)", "delta": -penalty})
    else:
        drivers.append({"label": "steady pace", "delta": 6})

    minutes = duration_seconds / 60 if duration_seconds > 0 else 0
    ppm = (pause_count / minutes) if minutes > 0 else 0
    if ppm > PAUSES_PER_MIN_OK:
        penalty = min(PAUSE_PENALTY_CAP, round((ppm - PAUSES_PER_MIN_OK) * 3))
    else:
        penalty = 0
    if penalty > 0:
        score -= penalty
        drivers.append({"label": "frequent long pauses", "delta": -penalty})
    else:
        drivers.append({"label": "good pause discipline", "delta": 4})

    return {"score": max(0, min(100, score)), "drivers": drivers}

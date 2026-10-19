"""Pinned 2026 slate for offline tests.

Do not read live ``data/schedule_2026.json``. A daily generate that writes
the Chargers final into the working tree must not flip Week 5 bye tests
or archive replay.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schedule_2026_frozen.json"


def frozen_schedule() -> list[dict]:
    """Deep copy of the checked-in pre-Chargers-final 2026 slate."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return copy.deepcopy([row for row in payload if isinstance(row, dict)])

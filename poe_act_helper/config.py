"""บันทึก/โหลดการตั้งค่าและสถานะ (ไว้ที่ ~/.poe_act_helper/state.json)."""
from __future__ import annotations

import json
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("POE_ACT_HELPER_HOME", Path.home() / ".poe_act_helper"))
STATE_FILE = STATE_DIR / "state.json"

DEFAULTS = {
    "log_path": "",
    "character": "",       # ว่าง = รับ level up ของทุกตัวละคร
    "league_start": True,
    "library": False,
    "bandit": "kill_all",
    "step_index": 0,
    "opacity": 0.88,
    "geometry": "",
    "char_level": 1,
    "area_level": 0,
    "area_code": "",
    "timer": {},
}


def load() -> dict:
    state = dict(DEFAULTS)
    try:
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return state


def save(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass

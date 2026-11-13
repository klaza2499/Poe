"""จับเวลาทั้งรัน + เวลาแยกต่อ Act และเทียบกับสถิติที่ดีที่สุด (PB)."""
from __future__ import annotations

import time


def fmt(sec: float | None, sign: bool = False) -> str:
    if sec is None:
        return "--:--"
    neg = sec < 0
    s = int(abs(sec))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    txt = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if sign:
        return ("-" if neg else "+") + txt
    return ("-" if neg else "") + txt


class SplitTimer:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.start_ts: float | None = None
        self.paused_at: float | None = None
        self.paused_total = 0.0
        self.splits: dict[int, float] = {}   # act -> เวลารวมตอนจบ act นั้น
        self.finished_at: float | None = None
        self.pb: dict[int, float] = {}       # สถิติดีที่สุดของแต่ละ act (เวลารวม)

    # ---------- state ----------
    @property
    def running(self) -> bool:
        return self.start_ts is not None and self.finished_at is None

    def start(self) -> None:
        if self.start_ts is None:
            self.start_ts = self.clock()

    def reset(self) -> None:
        self.start_ts = None
        self.paused_at = None
        self.paused_total = 0.0
        self.splits = {}
        self.finished_at = None

    def toggle_pause(self) -> None:
        if not self.running:
            return
        now = self.clock()
        if self.paused_at is None:
            self.paused_at = now
        else:
            self.paused_total += now - self.paused_at
            self.paused_at = None

    def elapsed(self) -> float:
        if self.start_ts is None:
            return 0.0
        end = self.finished_at or self.paused_at or self.clock()
        return max(0.0, end - self.start_ts - self.paused_total)

    # ---------- splits ----------
    def split(self, act: int) -> None:
        """บันทึกเวลาตอนจบ act"""
        if self.start_ts is not None and act not in self.splits:
            self.splits[act] = self.elapsed()

    def finish(self, last_act: int) -> None:
        if not self.running:
            return
        if self.paused_at is not None:
            self.toggle_pause()
        self.split(last_act)
        self.finished_at = self.clock()
        total_pb = self.pb.get(last_act)
        if total_pb is None or self.splits[last_act] < total_pb:
            self.pb = dict(self.splits)

    def act_start(self, act: int) -> float:
        prev = [t for a, t in self.splits.items() if a < act]
        return max(prev) if prev else 0.0

    def act_elapsed(self, act: int) -> float:
        if act in self.splits:
            return self.splits[act] - self.act_start(act)
        return self.elapsed() - self.act_start(act)

    def delta_vs_pb(self, act: int) -> float | None:
        if act in self.splits and act in self.pb:
            return self.splits[act] - self.pb[act]
        return None

    # ---------- persistence ----------
    def to_dict(self) -> dict:
        return {
            "start_ts": self.start_ts, "paused_at": self.paused_at,
            "paused_total": self.paused_total, "finished_at": self.finished_at,
            "splits": {str(k): v for k, v in self.splits.items()},
            "pb": {str(k): v for k, v in self.pb.items()},
        }

    def load_dict(self, d: dict) -> None:
        self.start_ts = d.get("start_ts")
        self.paused_at = d.get("paused_at")
        self.paused_total = d.get("paused_total", 0.0)
        self.finished_at = d.get("finished_at")
        self.splits = {int(k): v for k, v in d.get("splits", {}).items()}
        self.pb = {int(k): v for k, v in d.get("pb", {}).items()}

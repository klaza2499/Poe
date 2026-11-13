"""ตัวควบคุมหลัก (ไม่มี UI) — รับ event จาก log แล้วอัปเดตเส้นทาง/เวลา/เลเวล."""
from __future__ import annotations

from . import config
from .logwatch import AreaEvent, EnteredEvent, LevelEvent
from .route import Options, RouteTracker, load_steps
from .splits import SplitTimer

START_AREA = "1_1_1"  # The Twilight Strand — เริ่มจับเวลาอัตโนมัติ


class Controller:
    def __init__(self, state: dict | None = None, clock=None):
        self.state = state if state is not None else config.load()
        self.timer = SplitTimer() if clock is None else SplitTimer(clock)
        self.timer.load_dict(self.state.get("timer") or {})
        self.zone_name = ""
        self.reload_route(keep_index=True)

    # ---------- options ----------
    @property
    def options(self) -> Options:
        s = self.state
        return Options(league_start=s["league_start"], library=s["library"], bandit=s["bandit"])

    def reload_route(self, keep_index: bool = False) -> None:
        old = getattr(self, "tracker", None)
        steps = load_steps(self.options)
        idx = self.state.get("step_index", 0) if keep_index or old is None else 0
        if old is not None and not keep_index:
            # พยายามคงตำแหน่งเดิม (act + โซน) หลังเปลี่ยนตัวเลือก
            cur = old.current
            for i, s in enumerate(steps):
                if s.act == cur.act and s.area == cur.area:
                    idx = i
                    break
            else:
                idx = next((i for i, s in enumerate(steps) if s.act == cur.act), 0)
        self.tracker = RouteTracker(steps, idx)
        self.persist()

    # ---------- events ----------
    def handle(self, ev) -> bool:
        """คืน True ถ้าควรวาด UI ใหม่"""
        if isinstance(ev, AreaEvent):
            return self._on_area(ev)
        if isinstance(ev, LevelEvent):
            want = self.state.get("character", "").strip().lower()
            if want and ev.character.lower() != want:
                return False
            self.state["char_level"] = ev.level
            self.persist()
            return True
        if isinstance(ev, EnteredEvent):
            self.zone_name = ev.name
            return True
        return False

    def _on_area(self, ev: AreaEvent) -> bool:
        self.state["area_code"] = ev.code
        self.state["area_level"] = ev.level
        if ev.code == START_AREA and self.timer.start_ts is None and self.tracker.index == 0:
            self.timer.start()
        old_act = self.tracker.current.act
        moved = self.tracker.on_area(ev.code)
        if moved:
            self._after_move(old_act)
        self.persist()
        return True

    def _after_move(self, old_act: int) -> None:
        cur = self.tracker.current
        if cur.act > old_act:
            for a in range(old_act, cur.act):
                self.timer.split(a)
        if cur.finish:
            self.timer.finish(cur.act)

    # ---------- manual controls ----------
    def next_step(self) -> None:
        old = self.tracker.current.act
        self.tracker.next()
        self._after_move(old)
        self.persist()

    def prev_step(self) -> None:
        self.tracker.prev()
        self.persist()

    def jump_act(self, act: int) -> None:
        self.tracker.jump_to_act(act)
        self.persist()

    def reset_run(self) -> None:
        self.timer.reset()
        self.tracker.index = 0
        self.state["char_level"] = 1
        self.persist()

    def persist(self) -> None:
        self.state["step_index"] = self.tracker.index
        self.state["timer"] = self.timer.to_dict()
        config.save(self.state)

"""โหลดเส้นทางและติดตามว่าตอนนี้อยู่ขั้นไหน (เลื่อนอัตโนมัติเมื่อเข้าโซนใหม่)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROUTE = Path(__file__).with_name("data") / "route_poe1.json"
LOOKAHEAD = 8  # ค้นหาโซนถัดไปล่วงหน้ากี่ขั้น


@dataclass
class Options:
    league_start: bool = True   # ทำ Trial / Lab / เควสเสริมสำหรับเปิดลีก
    library: bool = False       # ทำเควส Library ใน Act 3 (ซื้อ gem จาก Siosa)
    bandit: str = "kill_all"    # kill_all | alira

    def check(self, cond: str | None) -> bool:
        if not cond:
            return True
        neg = cond.startswith("!")
        key = cond[1:] if neg else cond
        if key.startswith("bandit:"):
            val = self.bandit == key.split(":", 1)[1]
        else:
            val = bool(getattr(self, key, False))
        return val != neg


@dataclass
class Step:
    act: int
    area: str
    zone: str
    tasks: list[str]
    tips: list[str] = field(default_factory=list)
    lvl: str = ""
    finish: bool = False


def is_town(code: str) -> bool:
    return code.endswith("_town")


def is_ignored(code: str) -> bool:
    c = code.lower()
    return c.startswith("hideout") or "hideout" in c or c.startswith("map")


def load_steps(options: Options, path: Path = DEFAULT_ROUTE) -> list[Step]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = []
    for raw in data["steps"]:
        if not options.check(raw.get("only")):
            continue
        tasks = []
        for t in raw.get("tasks", []):
            if isinstance(t, str):
                tasks.append(t)
            elif options.check(t.get("only")):
                tasks.append(t["t"])
        steps.append(Step(
            act=raw["act"], area=raw["area"], zone=raw["zone"], tasks=tasks,
            tips=list(raw.get("tips", [])), lvl=str(raw.get("lvl", "")),
            finish=bool(raw.get("finish", False)),
        ))
    return steps


class RouteTracker:
    def __init__(self, steps: list[Step], index: int = 0):
        self.steps = steps
        self.index = max(0, min(index, len(steps) - 1))

    @property
    def current(self) -> Step:
        return self.steps[self.index]

    def upcoming(self, n: int = 2) -> list[Step]:
        return self.steps[self.index + 1 : self.index + 1 + n]

    def next(self) -> None:
        self.index = min(self.index + 1, len(self.steps) - 1)

    def prev(self) -> None:
        self.index = max(self.index - 1, 0)

    def jump_to_act(self, act: int) -> None:
        for i, s in enumerate(self.steps):
            if s.act == act:
                self.index = i
                return

    def on_area(self, code: str) -> bool:
        """เรียกเมื่อเข้าพื้นที่ใหม่ คืน True ถ้าเลื่อนขั้น

        กติกา:
        - เมือง (town) เลื่อนได้เฉพาะเมื่อเป็นขั้นถัดไปทันที เพราะผู้เล่น logout
          ผ่านเมืองบ่อยระหว่างทาง ไม่ควรกระโดดข้ามขั้น
        - โซนทั่วไปค้นล่วงหน้า LOOKAHEAD ขั้น
        - ถ้าไม่เจอ แต่โซนนั้นมีอยู่ขั้นเดียวในเส้นทาง (และอยู่ข้างหน้า)
          จะกระโดดไปเลย — ช่วยกรณีเปิดโปรแกรมกลางทาง
        """
        if is_ignored(code):
            return False
        if self.current.area == code:
            return False
        end = min(len(self.steps), self.index + 1 + LOOKAHEAD)
        for j in range(self.index + 1, end):
            if self.steps[j].area != code:
                continue
            if is_town(code) and j != self.index + 1:
                continue
            self.index = j
            return True
        if not is_town(code):
            hits = [j for j, s in enumerate(self.steps) if s.area == code]
            if len(hits) == 1 and hits[0] > self.index:
                self.index = hits[0]
                return True
        return False

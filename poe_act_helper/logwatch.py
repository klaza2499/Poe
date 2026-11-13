"""อ่าน Client.txt ของ Path of Exile แบบ real-time แล้วแปลงเป็น event."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

RE_GENERATE = re.compile(r'Generating level (\d+) area "([^"]+)"')
RE_ENTERED = re.compile(r"\] : You have entered (.+?)\.\s*$")
RE_LEVEL = re.compile(r"\] : (.+?) \((\w+)\) is now level (\d+)")


@dataclass
class AreaEvent:
    code: str
    level: int


@dataclass
class EnteredEvent:
    name: str


@dataclass
class LevelEvent:
    character: str
    char_class: str
    level: int


def parse_line(line: str):
    """คืนค่า event จากบรรทัด log หนึ่งบรรทัด หรือ None ถ้าไม่เกี่ยวข้อง"""
    m = RE_GENERATE.search(line)
    if m:
        return AreaEvent(code=m.group(2), level=int(m.group(1)))
    m = RE_LEVEL.search(line)
    if m:
        return LevelEvent(character=m.group(1), char_class=m.group(2), level=int(m.group(3)))
    m = RE_ENTERED.search(line)
    if m:
        return EnteredEvent(name=m.group(1))
    return None


def candidate_log_paths() -> list[Path]:
    roots = []
    for drive in ("C:", "D:", "E:", "F:"):
        roots += [
            Path(drive + "/Program Files (x86)/Grinding Gear Games/Path of Exile"),
            Path(drive + "/Program Files/Grinding Gear Games/Path of Exile"),
            Path(drive + "/Program Files (x86)/Steam/steamapps/common/Path of Exile"),
            Path(drive + "/Program Files/Steam/steamapps/common/Path of Exile"),
            Path(drive + "/SteamLibrary/steamapps/common/Path of Exile"),
            Path(drive + "/Steam/steamapps/common/Path of Exile"),
            Path(drive + "/Games/Path of Exile"),
        ]
    home = Path.home()
    roots += [
        home / ".steam/steam/steamapps/common/Path of Exile",
        home / ".local/share/Steam/steamapps/common/Path of Exile",
    ]
    out = []
    for r in roots:
        for name in ("Client.txt", "LatestClient.txt"):
            out.append(r / "logs" / name)
    return out


def find_log_path() -> Path | None:
    found = [p for p in candidate_log_paths() if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


class LogTailer:
    """ติดตามท้ายไฟล์ log (เหมือน tail -f) — เรียก poll() เป็นระยะ"""

    def __init__(self, path: str | os.PathLike, from_start: bool = False):
        self.path = Path(path)
        self._pos = 0
        self._buf = b""
        if not from_start and self.path.exists():
            self._pos = self.path.stat().st_size

    def poll(self) -> list:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self._pos:  # ไฟล์ถูกล้าง/สร้างใหม่
            self._pos = 0
            self._buf = b""
        if size == self._pos:
            return []
        with open(self.path, "rb") as f:
            f.seek(self._pos)
            data = f.read(size - self._pos)
            self._pos = f.tell()
        data = self._buf + data
        lines = data.split(b"\n")
        self._buf = lines.pop()  # บรรทัดที่ยังเขียนไม่จบ
        events = []
        for raw in lines:
            ev = parse_line(raw.decode("utf-8", errors="ignore").rstrip("\r"))
            if ev is not None:
                events.append(ev)
        return events

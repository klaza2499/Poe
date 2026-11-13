"""ค้นหา/บันทึกภาพ layout แมพของแต่ละโซน

วางภาพได้ 2 แบบ (png/gif/jpg) ในโฟลเดอร์ `layouts/` ข้างโปรแกรม หรือ ~/.poe_act_helper/layouts:
  layouts/1_1_3.png, layouts/1_1_3_b.png      (ชื่อขึ้นต้นด้วยรหัสโซน)
  layouts/1_1_3/อะไรก็ได้.png                 (โฟลเดอร์ชื่อรหัสโซน)
  layouts/The Mud Flats (Act 1)/....png        (หรือใช้ชื่อโซน + act)
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, quote_plus

from . import config

EXTS = {".png", ".gif", ".jpg", ".jpeg"}
PROJECT_LAYOUTS = Path(__file__).resolve().parent.parent / "layouts"


MANIFEST = Path(__file__).with_name("data") / "exile_ui_layouts.json"
RE_EXILE_UI = re.compile(r"^(?P<area>\S+) (?P<n>\d+)(?:_(?P<sub>\d+))?$")


def exile_ui_dir() -> Path:
    return config.STATE_DIR / "layouts" / "exile-ui"


def layout_dirs() -> list[Path]:
    return [PROJECT_LAYOUTS, config.STATE_DIR / "layouts", exile_ui_dir()]


def legend_path() -> Path | None:
    p = exile_ui_dir() / "explanation.jpg"
    return p if p.is_file() else None


def describe(path: Path) -> str:
    """อธิบายภาพจาก Exile-UI: '<area> N.jpg' = เบาะแส minimap ของแบบ N, '<area> N_M.jpg' = แผนที่ของแบบ N"""
    m = RE_EXILE_UI.match(path.stem)
    if not m:
        return path.name
    if m.group("sub"):
        return f"แบบ {m.group('n')} · แผนที่ {m.group('sub')}"
    return f"แบบ {m.group('n')} · เบาะแสบน minimap"


def download_exile_ui(progress=None, workers: int = 8) -> tuple[int, int]:
    """ดาวน์โหลดภาพ layout ทุกโซนของ PoE1 จาก Exile-UI (ข้ามไฟล์ที่มีอยู่แล้ว)
    คืน (จำนวนที่ดาวน์โหลดสำเร็จ, จำนวนที่ล้มเหลว)"""
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = "https://raw.githubusercontent.com/Lailloken/Exile-UI/{}/{}/".format(man["commit"], man["path"])
    dest = exile_ui_dir()
    dest.mkdir(parents=True, exist_ok=True)
    todo = [f for f in man["files"] if not (dest / f).is_file()]
    done = [0, 0]

    def get(name: str) -> None:
        try:
            req = urllib.request.Request(base + quote(name), headers={"User-Agent": "poe-act-helper"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            tmp = dest / (name + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest / name)
            done[0] += 1
        except Exception:
            done[1] += 1
        if progress:
            progress(done[0] + done[1], len(todo))

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(get, todo))
    return done[0], done[1]


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def find_layouts(area: str, zone: str = "", act: int | None = None) -> list[Path]:
    keys = {area.lower()}
    if zone:
        keys.add(_safe(f"{zone} (Act {act})" if act else zone).lower())
    out: list[Path] = []
    for d in layout_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir(), key=natural_key):
            stem = p.stem.lower() if p.is_file() else p.name.lower()
            hit = any(stem == k or stem.startswith(k + "_") or stem.startswith(k + " ") for k in keys)
            if not hit:
                continue
            if p.is_dir():
                out += sorted((f for f in p.iterdir() if f.suffix.lower() in EXTS), key=natural_key)
            elif p.suffix.lower() in EXTS:
                out.append(p)
    return list(dict.fromkeys(out))


def save_dir(area: str) -> Path:
    d = config.STATE_DIR / "layouts" / area
    d.mkdir(parents=True, exist_ok=True)
    return d


def capture_screen(area: str) -> Path:
    """แคปหน้าจอเก็บเป็น layout ของโซนนี้ (ต้องมี Pillow: pip install pillow)"""
    from PIL import ImageGrab  # type: ignore

    img = ImageGrab.grab(all_screens=False)
    path = save_dir(area) / time.strftime("%Y%m%d_%H%M%S.png")
    img.save(path)
    return path


def search_url(zone: str, act: int) -> str:
    return "https://www.google.com/search?tbm=isch&q=" + quote_plus(f"path of exile act {act} {zone} layout")

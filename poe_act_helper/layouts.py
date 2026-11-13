"""ค้นหา/บันทึกภาพ layout แมพของแต่ละโซน

วางภาพได้ 2 แบบ (png/gif/jpg) ในโฟลเดอร์ `layouts/` ข้างโปรแกรม หรือ ~/.poe_act_helper/layouts:
  layouts/1_1_3.png, layouts/1_1_3_b.png      (ชื่อขึ้นต้นด้วยรหัสโซน)
  layouts/1_1_3/อะไรก็ได้.png                 (โฟลเดอร์ชื่อรหัสโซน)
  layouts/The Mud Flats (Act 1)/....png        (หรือใช้ชื่อโซน + act)
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote_plus

from . import config

EXTS = {".png", ".gif", ".jpg", ".jpeg"}
PROJECT_LAYOUTS = Path(__file__).resolve().parent.parent / "layouts"


def layout_dirs() -> list[Path]:
    return [PROJECT_LAYOUTS, config.STATE_DIR / "layouts"]


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
        for p in sorted(d.iterdir()):
            stem = p.stem.lower() if p.is_file() else p.name.lower()
            hit = any(stem == k or stem.startswith(k + "_") or stem.startswith(k + " ") for k in keys)
            if not hit:
                continue
            if p.is_dir():
                out += sorted(f for f in p.iterdir() if f.suffix.lower() in EXTS)
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

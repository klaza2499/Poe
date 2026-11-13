# -*- coding: utf-8 -*-
"""ติดตั้งภาษาไทยให้ Exile-UI

ใช้: python install_thai.py "C:\\path\\to\\Exile-UI"      (ติดตั้ง)
     python install_thai.py "C:\\path\\to\\Exile-UI" --undo  (กลับเป็นอังกฤษ)

สิ่งที่ทำ:
1. สร้าง data/thai/UI.txt จาก data/english/UI.txt ของเครื่องคุณ (จึงตรงกับเวอร์ชันที่ใช้อยู่)
   ข้อความที่ยังไม่มีคำแปลจะคงเป็นภาษาอังกฤษ
2. คัดลอก data/english/client.txt ไป data/thai/ (ให้ "thai" ขึ้นในเมนูเลือกภาษา)
3. เปลี่ยนฟอนต์ใน data/english/client.txt เป็นฟอนต์ที่มีอักษรไทย (Fontin ไม่มีอักษรไทย)
   — สำรองไฟล์เดิมเป็น client.txt.bak
4. ตั้ง language=thai ใน ini/config.ini
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from translations import BY_KEY, FONT, T, TRANSLATOR  # noqa: E402

LANG = "thai"
# key ที่ Exile-UI ใช้ "อ่าน" ข้อความในไกด์ภาษาอังกฤษ ต้องคงเป็นอังกฤษ ไม่งั้นไกด์จะแสดงผิด
KEEP_ENGLISH_PREFIXES = ("lvltracker_format_", "lvltracker_recommended")

RX = re.compile(r'^(?P<pre>\s*(?P<key>[^\s;=][^=]*?)\s*=\s*")(?P<val>.*?)(?P<post>"\s*(;##.*)?)$')
RX_FONT = re.compile(r'^(\s*system_font\s*=\s*")[^"]*(".*)$')


def translate_ui(text: str) -> tuple[str, int, int]:
    out, done, total = [], 0, 0
    for line in text.split("\n"):
        m = RX.match(line.rstrip("\r"))
        if not m:
            out.append(line)
            continue
        key, val = m.group("key").strip(), m.group("val")
        total += 1
        new = None
        if key in BY_KEY:
            new = BY_KEY[key]
        elif key.startswith(KEEP_ENGLISH_PREFIXES):
            new = None
        elif val in T:
            new = T[val]
        if new is not None:
            done += 1
            cr = "\r" if line.endswith("\r") else ""
            line = m.group("pre") + new + m.group("post") + cr
        out.append(line)
        if key == "translator" and val == "translators:":
            pass
    text = "\n".join(out)
    # เพิ่มเครดิตผู้แปลหลังบรรทัด translator สุดท้าย
    idx = text.rfind("\ttranslator")
    if idx != -1:
        end = text.find("\n", idx)
        text = text[: end + 1] + f'\t\ttranslator\t\t\t=\t"{TRANSLATOR}"\n' + text[end + 1 :]
    return text, done, total


def set_font(path: Path, font: str) -> bool:
    raw = path.read_text(encoding="utf-8-sig")
    new = "\n".join(RX_FONT.sub(lambda m: m.group(1) + font + m.group(2), l) for l in raw.split("\n"))
    if new == raw:
        return False
    bak = path.with_name(path.name + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(new, encoding="utf-8-sig")
    return True


def set_ini_language(ini: Path, lang: str) -> None:
    lines = ini.read_text(encoding="utf-8-sig").splitlines() if ini.exists() else []
    out, in_settings, done = [], False, False
    for l in lines:
        s = l.strip().lower()
        if s.startswith("["):
            if in_settings and not done:
                out.append(f"language={lang}")
                done = True
            in_settings = s == "[settings]"
        elif in_settings and s.replace(" ", "").startswith("language="):
            l, done = f"language={lang}", True
        out.append(l)
    if not done:
        if not in_settings:
            out.append("[settings]")
        out.append(f"language={lang}")
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def install(root: Path) -> None:
    eng = root / "data" / "english"
    if not (eng / "UI.txt").is_file():
        raise SystemExit(f"ไม่พบ {eng / 'UI.txt'} — ใส่ path โฟลเดอร์ Exile-UI ให้ถูก (โฟลเดอร์ที่มี 'Exile UI.ahk')")
    th = root / "data" / LANG
    th.mkdir(exist_ok=True)
    text, done, total = translate_ui((eng / "UI.txt").read_text(encoding="utf-8-sig"))
    (th / "UI.txt").write_text(text, encoding="utf-8-sig")
    shutil.copy2(eng / "client.txt", th / "client.txt")
    set_font(th / "client.txt", FONT)
    set_font(eng / "client.txt", FONT)
    set_ini_language(root / "ini" / "config.ini", LANG)
    print(f"✔ แปลแล้ว {done}/{total} ข้อความ → {th / 'UI.txt'}")
    print(f"✔ ฟอนต์: {FONT}  |  ภาษา: {LANG}")
    print("เปิด Exile-UI ใหม่ (หรือกด restart) เพื่อใช้ภาษาไทย")


def undo(root: Path) -> None:
    eng = root / "data" / "english"
    bak = eng / "client.txt.bak"
    if bak.exists():
        shutil.copy2(bak, eng / "client.txt")
        bak.unlink()
    set_ini_language(root / "ini" / "config.ini", "english")
    print("✔ กลับเป็นภาษาอังกฤษแล้ว (โฟลเดอร์ data/thai ยังอยู่ ลบเองได้)")


def main(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        p = input("ลากโฟลเดอร์ Exile-UI มาวางที่นี่ แล้วกด Enter: ").strip().strip('"')
        args = [p]
    root = Path(args[0])
    (undo if "--undo" in argv else install)(root)


if __name__ == "__main__":
    main(sys.argv[1:])

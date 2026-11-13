"""หน้าต่างดูภาพ layout แมพของโซนปัจจุบัน (เปลี่ยนตามโซนอัตโนมัติ)."""
from __future__ import annotations

import math
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from pathlib import Path

from . import layouts

MAX_W, MAX_H = 720, 520
BG, FG, DIM, GOLD = "#14161b", "#e8e6e3", "#8b8f98", "#d9b45a"
FONT = "Leelawadee UI" if sys.platform == "win32" else "Tahoma"


def load_photo(path: Path, master):
    """โหลดภาพแล้วย่อให้พอดี — ใช้ Pillow ถ้ามี ไม่งั้นใช้ PhotoImage (png/gif)"""
    try:
        from PIL import Image, ImageTk  # type: ignore

        img = Image.open(path)
        img.thumbnail((MAX_W, MAX_H))
        return ImageTk.PhotoImage(img, master=master)
    except ImportError:
        img = tk.PhotoImage(file=str(path), master=master)
        f = max(1, math.ceil(max(img.width() / MAX_W, img.height() / MAX_H)))
        return img.subsample(f, f) if f > 1 else img


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class LayoutViewer:
    def __init__(self, root: tk.Tk, get_step, notify):
        self.root = root
        self.get_step = get_step
        self.notify = notify
        self.win: tk.Toplevel | None = None
        self.files: list[Path] = []
        self.i = 0
        self._area = None
        self._photo = None

    def count(self, step) -> int:
        return len(layouts.find_layouts(step.area, step.zone, step.act))

    # ---------------------------------------------------------------- window
    def toggle(self) -> None:
        if self.win is not None:
            self.close()
        else:
            self.open()

    def open(self) -> None:
        w = self.win = tk.Toplevel(self.root, bg=BG)
        w.title("Layout")
        w.attributes("-topmost", True)
        w.geometry(f"+{self.root.winfo_x() + self.root.winfo_width() + 8}+{self.root.winfo_y()}")
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<Left>", lambda e: self.move(-1))
        w.bind("<Right>", lambda e: self.move(1))
        w.protocol("WM_DELETE_WINDOW", self.close)
        try:
            w.attributes("-alpha", 0.95)
        except tk.TclError:
            pass
        self.title = tk.Label(w, bg=BG, fg=GOLD, font=(FONT, 11, "bold"))
        self.title.pack(fill="x", padx=8, pady=(6, 2))
        self.img = tk.Label(w, bg=BG, fg=DIM, font=(FONT, 10), justify="center", cursor="hand2")
        self.img.pack(padx=8, pady=4)
        self.img.bind("<Button-1>", lambda e: self.move(1))
        bar = tk.Frame(w, bg=BG)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        for txt, cmd in (("◀", lambda: self.move(-1)), ("▶", lambda: self.move(1)),
                         ("📷 แคปหน้าจอ", self.capture), ("📁 โฟลเดอร์", self.open_dir),
                         ("🔎 ค้นหาบนเว็บ", self.search), ("✕", self.close)):
            b = tk.Label(bar, text=txt, bg="#1d2027", fg=FG, font=(FONT, 9), padx=8, pady=2,
                         cursor="hand2")
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda e, c=cmd: c())
        self._area = None
        self.refresh(self.get_step())

    def close(self) -> None:
        if self.win is not None:
            self.win.destroy()
        self.win = None

    def refresh(self, step, force: bool = False) -> None:
        if self.win is None or (step.area == self._area and not force):
            return
        self._area = step.area
        self.files = layouts.find_layouts(step.area, step.zone, step.act)
        self.i = 0
        self.show()

    def move(self, d: int) -> None:
        if self.files:
            self.i = (self.i + d) % len(self.files)
            self.show()

    def show(self) -> None:
        step = self.get_step()
        if not self.files:
            self._photo = None
            self.title.configure(text=f"{step.zone} — ยังไม่มีภาพ layout")
            self.img.configure(image="", text=(
                "วางภาพ (png/jpg) ไว้ในโฟลเดอร์ 📁 แล้วกดเปิดใหม่\n"
                "หรือกด Tab ในเกมเปิดแมพ แล้วกด Ctrl+Alt+S / 📷 เพื่อแคปเก็บไว้\n"
                f"(ชื่อไฟล์ขึ้นต้นด้วยรหัสโซน: {step.area})"))
            return
        f = self.files[self.i]
        self.title.configure(text=f"{step.zone}  ·  แบบ {self.i + 1}/{len(self.files)}  ·  {f.name}")
        try:
            self._photo = load_photo(f, self.win)
            self.img.configure(image=self._photo, text="")
        except Exception as e:  # ไฟล์เสีย/ไม่รองรับ (jpg ต้องมี Pillow)
            self._photo = None
            self.img.configure(image="", text=f"เปิดภาพไม่ได้: {f.name}\n{e}\n(ไฟล์ jpg ต้อง pip install pillow)")

    # ---------------------------------------------------------------- actions
    def capture(self) -> None:
        step = self.get_step()
        was_open = self.win is not None
        self.close()
        self.root.withdraw()  # ซ่อน overlay ไม่ให้ติดในภาพ

        def grab():
            try:
                p = layouts.capture_screen(step.area)
                msg = f"📷 บันทึก layout แล้ว: {p.name}"
            except ImportError:
                msg = "📷 ต้องติดตั้ง Pillow ก่อน: pip install pillow"
            except Exception as e:
                msg = f"📷 แคปไม่สำเร็จ: {e}"
            self.root.deiconify()
            if was_open:
                self.open()
            self.notify(msg)

        self.root.after(250, grab)

    def open_dir(self) -> None:
        step = self.get_step()
        d = layouts.save_dir(step.area)
        try:
            open_folder(d)
        except Exception:
            self.notify(f"โฟลเดอร์ layout: {d}")

    def search(self) -> None:
        step = self.get_step()
        webbrowser.open(layouts.search_url(step.zone, step.act))

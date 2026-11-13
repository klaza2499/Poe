"""Overlay หน้าต่างลอยบนเกม (tkinter) — ลากย้ายได้, โปร่งใส, อยู่บนสุดเสมอ."""
from __future__ import annotations

import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .app import Controller
from .logwatch import LogTailer, find_log_path
from .splits import fmt
from .xp import level_window, xp_multiplier

FONT = "Leelawadee UI" if sys.platform == "win32" else "Tahoma"
BG = "#14161b"
BG2 = "#1d2027"
FG = "#e8e6e3"
DIM = "#8b8f98"
GOLD = "#d9b45a"
GREEN = "#6fcf7c"
RED = "#ef6b6b"
ORANGE = "#f0a54a"
WIDTH = 360
POLL_MS = 300


class Overlay:
    def __init__(self, ctrl: Controller, log_path: str | None = None):
        self.ctrl = ctrl
        self.events: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.title("PoE Act Helper")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self._set_alpha(ctrl.state.get("opacity", 0.88))
        geo = ctrl.state.get("geometry") or ""
        pos = geo[geo.index("+"):] if "+" in geo else "+40+120"
        self.root.geometry(f"{WIDTH}x10{pos}")
        self._build()
        self.tailer: LogTailer | None = None
        self._open_log(log_path or ctrl.state.get("log_path") or None)
        self._register_hotkeys()
        self.render()
        self.root.after(POLL_MS, self._tick)
        self.root.after(500, self._tick_timer)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        r = self.root
        head = tk.Frame(r, bg=BG2)
        head.pack(fill="x")
        self.lbl_act = tk.Label(head, bg=BG2, fg=GOLD, font=(FONT, 11, "bold"))
        self.lbl_act.pack(side="left", padx=(8, 4), pady=3)
        self.lbl_time = tk.Label(head, bg=BG2, fg=FG, font=("Consolas", 11, "bold"))
        self.lbl_time.pack(side="left", padx=4)
        for txt, cmd, tip in (("✕", self.quit, "ปิด"), ("⚙", self.open_settings, "ตั้งค่า"),
                              ("⏯", self._pause, "หยุด/เดินเวลา"), ("▶", self._next, "ขั้นถัดไป"),
                              ("◀", self._prev, "ขั้นก่อนหน้า")):
            b = tk.Label(head, text=txt, bg=BG2, fg=DIM, font=(FONT, 11), cursor="hand2", padx=5)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>", lambda e, w=b: w.configure(fg=FG))
            b.bind("<Leave>", lambda e, w=b: w.configure(fg=DIM))
        for w in (head, self.lbl_act, self.lbl_time):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", lambda e: self._save_geometry())

        body = tk.Frame(r, bg=BG, padx=10, pady=6)
        body.pack(fill="both", expand=True)
        self.lbl_zone = tk.Label(body, bg=BG, fg=FG, font=(FONT, 12, "bold"), anchor="w",
                                 justify="left", wraplength=WIDTH - 20)
        self.lbl_zone.pack(fill="x")
        self.lbl_xp = tk.Label(body, bg=BG, fg=DIM, font=(FONT, 9), anchor="w")
        self.lbl_xp.pack(fill="x")
        self.tasks = tk.Frame(body, bg=BG)
        self.tasks.pack(fill="x", pady=(6, 2))
        self.lbl_tips = tk.Label(body, bg=BG, fg=DIM, font=(FONT, 9, "italic"), anchor="w",
                                 justify="left", wraplength=WIDTH - 20)
        self.lbl_tips.pack(fill="x")
        tk.Frame(body, bg=BG2, height=1).pack(fill="x", pady=5)
        self.lbl_next = tk.Label(body, bg=BG, fg=DIM, font=(FONT, 9), anchor="w",
                                 justify="left", wraplength=WIDTH - 20)
        self.lbl_next.pack(fill="x")
        self.lbl_split = tk.Label(body, bg=BG, fg=DIM, font=("Consolas", 9), anchor="w",
                                  justify="left")
        self.lbl_split.pack(fill="x")
        self.lbl_status = tk.Label(body, bg=BG, fg=ORANGE, font=(FONT, 8), anchor="w",
                                   justify="left", wraplength=WIDTH - 20)
        self.lbl_status.pack(fill="x")

        r.bind("<Right>", lambda e: self._next())
        r.bind("<Left>", lambda e: self._prev())
        r.bind("<Button-3>", lambda e: self.open_settings())

    # ------------------------------------------------------------------ render
    def render(self) -> None:
        c = self.ctrl
        t = c.tracker
        step = t.current
        self.lbl_act.configure(text=f"ACT {step.act}  ·  {t.index + 1}/{len(t.steps)}")
        lvl = f"   (แนะนำ lv {step.lvl})" if step.lvl else ""
        self.lbl_zone.configure(text=f"📍 {step.zone}{lvl}")

        for w in self.tasks.winfo_children():
            w.destroy()
        for task in step.tasks:
            color = GOLD if "Passive" in task else (ORANGE if task.startswith("Logout") else FG)
            tk.Label(self.tasks, text="• " + task, bg=BG, fg=color, font=(FONT, 10),
                     anchor="w", justify="left", wraplength=WIDTH - 24).pack(fill="x", anchor="w")
        self.lbl_tips.configure(text="\n".join("💡 " + x for x in step.tips))

        ups = t.upcoming(2)
        self.lbl_next.configure(text="ถัดไป: " + "  →  ".join(s.zone for s in ups) if ups else "")
        self._render_xp()
        self._render_time()
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry(f"{WIDTH}x{h}+{x}+{y}")

    def _render_xp(self) -> None:
        s = self.ctrl.state
        cl, al = int(s.get("char_level") or 1), int(s.get("area_level") or 0)
        if not al:
            self.lbl_xp.configure(text=f"ตัวละคร lv {cl}", fg=DIM)
            return
        mult = xp_multiplier(cl, al)
        lo, hi = level_window(cl)
        if mult >= 0.999:
            color, note = GREEN, "XP เต็ม"
        elif mult >= 0.7:
            color, note = ORANGE, f"XP {mult:.0%}"
        else:
            color, note = RED, f"XP {mult:.0%}"
        hint = ""
        if al > hi:
            hint = " — เลเวลต่ำไป"
        elif al < lo:
            hint = " — เลเวลเกิน รีบไปต่อ!"
        self.lbl_xp.configure(text=f"ตัวละคร lv {cl}  ·  พื้นที่ lv {al}  ·  {note}{hint}", fg=color)

    def _render_time(self) -> None:
        tm = self.ctrl.timer
        act = self.ctrl.tracker.current.act
        paused = " ⏸" if tm.paused_at is not None else ""
        self.lbl_time.configure(text=f"{fmt(tm.elapsed())}{paused}",
                                fg=GREEN if tm.finished_at else FG)
        lines = [f"Act {act}: {fmt(tm.act_elapsed(act))}"]
        if act in tm.pb:
            pb_act = tm.pb[act] - max([v for a, v in tm.pb.items() if a < act] or [0])
            lines[0] += f"   (PB {fmt(pb_act)})"
        last = max(tm.splits) if tm.splits else None
        if last is not None:
            d = tm.delta_vs_pb(last)
            dtxt = f"  {fmt(d, sign=True)} vs PB" if d is not None else ""
            lines.append(f"จบ Act {last} ที่ {fmt(tm.splits[last])}{dtxt}")
        if tm.start_ts is None:
            lines.append("⏱ เวลาจะเริ่มเองเมื่อเข้า The Twilight Strand")
        self.lbl_split.configure(text="\n".join(lines))

    # ------------------------------------------------------------------ loop
    def _tick(self) -> None:
        changed = False
        if self.tailer:
            for ev in self.tailer.poll():
                changed |= self.ctrl.handle(ev)
        while not self.events.empty():
            self.events.get_nowait()()
            changed = True
        if changed:
            self.render()
        self.root.after(POLL_MS, self._tick)

    def _tick_timer(self) -> None:
        self._render_time()
        self.root.after(500, self._tick_timer)

    # ------------------------------------------------------------------ actions
    def _next(self):
        self.ctrl.next_step()
        self.render()

    def _prev(self):
        self.ctrl.prev_step()
        self.render()

    def _pause(self):
        self.ctrl.timer.toggle_pause()
        self.ctrl.persist()
        self._render_time()

    def quit(self):
        self._save_geometry()
        self.ctrl.persist()
        self.root.destroy()

    def _set_alpha(self, a: float) -> None:
        try:
            self.root.attributes("-alpha", float(a))
        except tk.TclError:
            pass

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _save_geometry(self):
        self.ctrl.state["geometry"] = f"{WIDTH}x10+{self.root.winfo_x()}+{self.root.winfo_y()}"
        self.ctrl.persist()

    def _open_log(self, path: str | None) -> None:
        if not path:
            found = find_log_path()
            path = str(found) if found else None
        if not path:
            self.tailer = None
            self.lbl_status.configure(
                text="⚠ ไม่พบ Client.txt — คลิก ⚙ แล้วเลือกไฟล์ (อยู่ในโฟลเดอร์เกม/logs/Client.txt)")
            return
        self.tailer = LogTailer(path)
        self.ctrl.state["log_path"] = path
        self.ctrl.persist()
        self.lbl_status.configure(text="")

    def _register_hotkeys(self) -> None:
        try:
            import keyboard  # type: ignore  (ตัวเลือกเสริม: pip install keyboard)
        except Exception:
            return
        put = self.events.put
        try:
            keyboard.add_hotkey("ctrl+alt+right", lambda: put(self.ctrl.next_step))
            keyboard.add_hotkey("ctrl+alt+left", lambda: put(self.ctrl.prev_step))
            keyboard.add_hotkey("ctrl+alt+p", lambda: put(self.ctrl.timer.toggle_pause))
        except Exception:
            pass

    # ------------------------------------------------------------------ settings
    def open_settings(self) -> None:
        s = self.ctrl.state
        win = tk.Toplevel(self.root)
        win.title("ตั้งค่า PoE Act Helper")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both")

        ttk.Label(frm, text="ไฟล์ Client.txt").grid(row=0, column=0, sticky="w", **pad)
        v_log = tk.StringVar(value=s.get("log_path", ""))
        ttk.Entry(frm, textvariable=v_log, width=46).grid(row=0, column=1, **pad)

        def browse():
            p = filedialog.askopenfilename(parent=win, title="เลือก Client.txt",
                                           filetypes=[("PoE log", "*.txt"), ("All", "*.*")])
            if p:
                v_log.set(p)
        ttk.Button(frm, text="เลือก…", command=browse).grid(row=0, column=2, **pad)

        ttk.Label(frm, text="ชื่อตัวละคร (ไม่บังคับ)").grid(row=1, column=0, sticky="w", **pad)
        v_char = tk.StringVar(value=s.get("character", ""))
        ttk.Entry(frm, textvariable=v_char, width=24).grid(row=1, column=1, sticky="w", **pad)

        v_league = tk.BooleanVar(value=s["league_start"])
        ttk.Checkbutton(frm, text="โหมดเปิดลีก (ทำ Trial/Lab + เควสเสริมเก็บของ)",
                        variable=v_league).grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        v_lib = tk.BooleanVar(value=s["library"])
        ttk.Checkbutton(frm, text="ทำเควส Library (Act 3) เพื่อซื้อ gem จาก Siosa",
                        variable=v_lib).grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frm, text="Bandits (Act 2)").grid(row=4, column=0, sticky="w", **pad)
        v_bandit = tk.StringVar(value=s["bandit"])
        bf = ttk.Frame(frm)
        bf.grid(row=4, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(bf, text="ฆ่าทั้งหมด (+Passive)", value="kill_all",
                        variable=v_bandit).pack(side="left", padx=4)
        ttk.Radiobutton(bf, text="ช่วย Alira", value="alira",
                        variable=v_bandit).pack(side="left", padx=4)

        ttk.Label(frm, text="ความทึบหน้าต่าง").grid(row=5, column=0, sticky="w", **pad)
        v_alpha = tk.DoubleVar(value=s.get("opacity", 0.88))
        ttk.Scale(frm, from_=0.3, to=1.0, variable=v_alpha,
                  command=lambda v: self._set_alpha(float(v))).grid(row=5, column=1, sticky="we", **pad)

        ttk.Label(frm, text="กระโดดไป Act").grid(row=6, column=0, sticky="w", **pad)
        v_act = tk.IntVar(value=self.ctrl.tracker.current.act)
        af = ttk.Frame(frm)
        af.grid(row=6, column=1, sticky="w")
        ttk.Spinbox(af, from_=1, to=10, textvariable=v_act, width=4).pack(side="left", padx=4)

        def jump():
            self.ctrl.jump_act(int(v_act.get()))
            self.render()
        ttk.Button(af, text="ไป", command=jump).pack(side="left")

        def reset():
            if messagebox.askyesno("รีเซ็ต", "เริ่มรันใหม่? (เวลาและขั้นตอนจะกลับไปเริ่มต้น, PB ยังอยู่)",
                                   parent=win):
                self.ctrl.reset_run()
                self.render()

        def save():
            route_changed = (v_league.get() != s["league_start"] or v_lib.get() != s["library"]
                             or v_bandit.get() != s["bandit"])
            s.update(character=v_char.get().strip(), league_start=v_league.get(),
                     library=v_lib.get(), bandit=v_bandit.get(), opacity=round(v_alpha.get(), 2))
            if v_log.get().strip() != (s.get("log_path") or "") or self.tailer is None:
                self._open_log(v_log.get().strip() or None)
            if route_changed:
                self.ctrl.reload_route()
            self.ctrl.persist()
            self.render()
            win.destroy()

        bar = ttk.Frame(frm)
        bar.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="รีเซ็ตรัน", command=reset).pack(side="left", padx=4)
        ttk.Button(bar, text="บันทึก", command=save).pack(side="left", padx=4)

    def run(self) -> None:
        self.root.mainloop()

"""เริ่มโปรแกรม: python -m poe_act_helper [--log PATH] [--simulate]"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="poe_act_helper", description="PoE Act Helper — overlay ช่วยวิ่ง Act")
    p.add_argument("--log", help="path ของ Client.txt (ถ้าไม่ใส่จะหาเอง)")
    p.add_argument("--simulate", action="store_true",
                   help="ทดลองแบบไม่ต้องเปิดเกม: พิมพ์รหัสพื้นที่ทีละบรรทัดใน terminal")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)

    from .app import Controller
    ctrl = Controller()

    if args.simulate:
        return simulate(ctrl)

    try:
        from .ui import Overlay
    except ImportError as e:  # ไม่มี tkinter
        print("ต้องใช้ tkinter (มากับ Python ที่ติดตั้งจาก python.org):", e, file=sys.stderr)
        return 1
    Overlay(ctrl, args.log).run()
    return 0


def simulate(ctrl) -> int:
    from .logwatch import AreaEvent, LevelEvent

    def show():
        s = ctrl.tracker.current
        print(f"\n[Act {s.act} · {ctrl.tracker.index + 1}/{len(ctrl.tracker.steps)}] {s.zone} ({s.area})")
        for t in s.tasks:
            print("  •", t)
        for t in s.tips:
            print("  💡", t)

    print("พิมพ์รหัสพื้นที่ เช่น 1_1_1, 'lv 12', 'n' = ถัดไป, 'p' = ก่อนหน้า, 'q' = ออก")
    show()
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "q":
            break
        if cmd == "n":
            ctrl.next_step()
        elif cmd == "p":
            ctrl.prev_step()
        elif cmd.startswith("lv "):
            ctrl.handle(LevelEvent("sim", "Sim", int(cmd[3:])))
        elif cmd:
            ctrl.handle(AreaEvent(cmd, int(ctrl.state.get("area_level") or 1)))
        show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

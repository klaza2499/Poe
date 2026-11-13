import itertools
import os
import tempfile
import unittest
from pathlib import Path

os.environ["POE_ACT_HELPER_HOME"] = tempfile.mkdtemp()

from poe_act_helper import config  # noqa: E402
from poe_act_helper.app import Controller  # noqa: E402
from poe_act_helper.logwatch import AreaEvent, EnteredEvent, LevelEvent, LogTailer, parse_line  # noqa: E402
from poe_act_helper.route import Options, RouteTracker, load_steps  # noqa: E402
from poe_act_helper.splits import SplitTimer, fmt  # noqa: E402
from poe_act_helper.xp import xp_multiplier  # noqa: E402

GEN = '2024/07/26 20:15:32 123 cffb0719 [DEBUG Client 1] Generating level {lvl} area "{code}" with seed 1'
LVL = "2024/07/26 20:15:33 123 bb3 [INFO Client 1] : {name} (Witch) is now level {lvl}"
ENT = "2024/07/26 20:15:33 123 bb3 [INFO Client 1] : You have entered The Coast."

ALL_OPTIONS = [Options(league_start=ls, library=lib, bandit=b)
               for ls, lib, b in itertools.product([True, False], [True, False], ["kill_all", "alira"])]


class ParseTests(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(parse_line(GEN.format(lvl=2, code="1_1_2")), AreaEvent("1_1_2", 2))
        self.assertEqual(parse_line(LVL.format(name="Foo Bar", lvl=12)), LevelEvent("Foo Bar", "Witch", 12))
        self.assertEqual(parse_line(ENT), EnteredEvent("The Coast"))
        self.assertIsNone(parse_line("random line"))

    def test_tailer_reads_only_new_complete_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "Client.txt"
            p.write_text(GEN.format(lvl=1, code="old") + "\n", encoding="utf-8")
            t = LogTailer(p)
            self.assertEqual(t.poll(), [])
            with open(p, "a", encoding="utf-8") as f:
                f.write(GEN.format(lvl=1, code="1_1_1") + "\n" + LVL.format(name="X", lvl=2)[:20])
            self.assertEqual(t.poll(), [AreaEvent("1_1_1", 1)])
            with open(p, "a", encoding="utf-8") as f:
                f.write(LVL.format(name="X", lvl=2)[20:] + "\n")
            self.assertEqual(t.poll(), [LevelEvent("X", "Witch", 2)])


class RouteTests(unittest.TestCase):
    def test_route_data_sane(self):
        for opt in ALL_OPTIONS:
            steps = load_steps(opt)
            self.assertTrue(steps[0].area == "1_1_1" and steps[-1].finish)
            self.assertEqual(sorted({s.act for s in steps}), list(range(1, 11)))
            for a, b in zip(steps, steps[1:]):
                self.assertNotEqual(a.area, b.area, f"ขั้นติดกันโซนเดียวกัน: {a.zone} ({opt})")
                self.assertLessEqual(a.act, b.act)
            for s in steps:
                self.assertTrue(s.tasks, f"ไม่มีงานใน {s.zone}")

    def test_options_filter(self):
        ls = load_steps(Options(league_start=True))
        no = load_steps(Options(league_start=False))
        self.assertIn("1_1_2a", [s.area for s in ls])
        self.assertNotIn("1_1_2a", [s.area for s in no])
        self.assertNotIn("Labyrinth_Airlock", [s.area for s in no])

    def test_full_playthrough_auto_advances_every_step(self):
        for opt in ALL_OPTIONS:
            steps = load_steps(opt)
            tr = RouteTracker(steps)
            for j in range(1, len(steps)):
                tr.on_area(steps[j].area)
                self.assertEqual(tr.index, j, f"ค้างที่ {steps[tr.index].zone} ไม่ไป {steps[j].zone} ({opt})")

    def test_passing_through_town_does_not_skip(self):
        steps = load_steps(Options(league_start=False))
        tr = RouteTracker(steps)
        # หา step ที่ขั้นถัดไปไม่ใช่เมือง แล้วลอง logout ผ่านเมือง
        i = next(i for i, s in enumerate(steps) if s.area == "1_2_4")  # Broken Bridge
        tr.index = i
        tr.on_area("1_2_town")
        self.assertEqual(tr.index, i)
        tr.on_area("1_2_7")
        self.assertEqual(steps[tr.index].area, "1_2_7")

    def test_hideout_ignored_and_backtrack_no_jump(self):
        steps = load_steps(Options())
        tr = RouteTracker(steps, 10)
        self.assertFalse(tr.on_area("HideoutFelled"))
        self.assertFalse(tr.on_area("1_1_1"))
        self.assertEqual(tr.index, 10)

    def test_resync_unique_zone_far_ahead(self):
        steps = load_steps(Options())
        tr = RouteTracker(steps)
        tr.on_area("1_3_1")  # City of Sarn — มีขั้นเดียว
        self.assertEqual(tr.current.area, "1_3_1")


class XpTests(unittest.TestCase):
    def test_multiplier(self):
        self.assertEqual(xp_multiplier(10, 10), 1.0)
        self.assertEqual(xp_multiplier(10, 13), 1.0)  # safe range = 3
        self.assertLess(xp_multiplier(10, 20), 0.5)
        self.assertLess(xp_multiplier(30, 15), 0.5)


class TimerTests(unittest.TestCase):
    def test_splits_pause_pb(self):
        now = [0.0]
        t = SplitTimer(clock=lambda: now[0])
        t.start()
        now[0] = 100
        t.split(1)
        t.toggle_pause()
        now[0] = 150
        t.toggle_pause()
        now[0] = 250
        self.assertEqual(t.elapsed(), 200)
        self.assertEqual(t.act_elapsed(2), 100)
        t.finish(2)
        self.assertEqual(t.pb, {1: 100, 2: 200})
        t.reset()
        t.start()
        now[0] = 340
        t.split(1)
        self.assertEqual(t.delta_vs_pb(1), -10)
        self.assertEqual(fmt(-10, sign=True), "-00:10")
        self.assertEqual(fmt(3725), "1:02:05")


class ControllerTests(unittest.TestCase):
    def test_flow(self):
        now = [1000.0]
        ctrl = Controller(state=dict(config.DEFAULTS), clock=lambda: now[0])
        ctrl.handle(AreaEvent("1_1_1", 1))
        self.assertTrue(ctrl.timer.running)
        ctrl.handle(LevelEvent("X", "Witch", 5))
        self.assertEqual(ctrl.state["char_level"], 5)
        for s in ctrl.tracker.steps[1:]:
            now[0] += 60
            ctrl.handle(AreaEvent(s.area, 10))
        self.assertTrue(ctrl.tracker.current.finish)
        self.assertIsNotNone(ctrl.timer.finished_at)
        self.assertEqual(sorted(ctrl.timer.splits), list(range(1, 11)))
        self.assertEqual(sorted(ctrl.timer.pb), list(range(1, 11)))

    def test_character_filter(self):
        st = dict(config.DEFAULTS, character="Me")
        ctrl = Controller(state=st)
        ctrl.handle(LevelEvent("Other", "Witch", 30))
        self.assertEqual(ctrl.state["char_level"], 1)
        ctrl.handle(LevelEvent("me", "Witch", 3))
        self.assertEqual(ctrl.state["char_level"], 3)


class LayoutTests(unittest.TestCase):
    def test_find_layouts(self):
        from poe_act_helper import layouts
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "1_1_3.png").write_bytes(b"x")
            (d / "1_1_3_b.png").write_bytes(b"x")
            (d / "1_1_30.png").write_bytes(b"x")          # โซนอื่น ห้ามติดมา
            (d / "1_1_3" ).mkdir()
            (d / "1_1_3" / "c.jpg").write_bytes(b"x")
            (d / "1_1_3" / "notes.txt").write_bytes(b"x")
            (d / "The Coast (Act 1)").mkdir()
            (d / "The Coast (Act 1)" / "a.png").write_bytes(b"x")
            old = layouts.layout_dirs
            layouts.layout_dirs = lambda: [d]
            try:
                got = [p.name for p in layouts.find_layouts("1_1_3", "The Mud Flats", 1)]
                self.assertEqual(sorted(got), ["1_1_3.png", "1_1_3_b.png", "c.jpg"])
                self.assertEqual([p.name for p in layouts.find_layouts("1_1_2", "The Coast", 1)], ["a.png"])
                self.assertEqual(layouts.find_layouts("2_6_2", "The Coast", 6), [])
            finally:
                layouts.layout_dirs = old


# โซนที่ Exile-UI ไม่มีภาพ (ทางตรง/เล็ก/ไม่ใช่โซนสุ่ม)
NO_LAYOUT = {"1_1_1", "1_2_2a", "2_8_10", "Labyrinth_Airlock"}


class ExileUiLayoutTests(unittest.TestCase):
    def test_manifest_covers_every_route_zone(self):
        import json
        from poe_act_helper import layouts
        from poe_act_helper.route import is_town
        man = json.loads(layouts.MANIFEST.read_text(encoding="utf-8"))
        areas = {f.split(" ")[0] for f in man["files"]}
        for s in load_steps(Options(league_start=True, library=True)):
            if not is_town(s.area) and s.area not in NO_LAYOUT:
                self.assertIn(s.area, areas, s.zone)

    def test_describe_and_order(self):
        from poe_act_helper import layouts
        self.assertEqual(layouts.describe(Path("1_1_2 3.jpg")), "แบบ 3 · เบาะแสบน minimap")
        self.assertEqual(layouts.describe(Path("1_1_2 3_2.jpg")), "แบบ 3 · แผนที่ 2")
        names = ["1_1_2 10.jpg", "1_1_2 2.jpg", "1_1_2 1_1.jpg", "1_1_2 1.jpg"]
        got = [p.name for p in sorted(map(Path, names), key=layouts.natural_key)]
        self.assertEqual(got, ["1_1_2 1.jpg", "1_1_2 1_1.jpg", "1_1_2 2.jpg", "1_1_2 10.jpg"])


if __name__ == "__main__":
    unittest.main()

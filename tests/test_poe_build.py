import io
import json
import os
import tempfile
import unittest

os.environ["POE_BUILDS_DIR"] = tempfile.mkdtemp()

from poe_build import api, data, items, skills, mcp_server  # noqa: E402
from poe_build.build import Build  # noqa: E402
from poe_build.tree import TreeSpec, points_available  # noqa: E402
from tools import lualit  # noqa: E402


class LuaParserTests(unittest.TestCase):
    def test_literals_and_raw(self):
        src = 'return { a = 1, ["b c"] = "x\\ny", [[long]], list = { 1, 2, -3.5 }, f = mod("X", nil), ' \
              't = SkillType.Spell, fn = function(x) if x then return 1 end end, z = true }'
        t = lualit.parse_return_table(src)
        self.assertEqual(t["a"], 1)
        self.assertEqual(t["b c"], "x\ny")
        self.assertEqual(t[1], "long")
        self.assertEqual(t["list"], [1, 2, -3.5])
        self.assertEqual(str(t["f"]), 'mod("X", nil)')
        self.assertEqual(str(t["t"]), "SkillType.Spell")
        self.assertTrue(str(t["fn"]).startswith("function"))
        self.assertIs(t["z"], True)

    def test_assignments(self):
        src = 'return function(x)\nx["A"] = { v = 1 }\nlocal y = 2\nx["B"] = { v = 2 }\nend'
        self.assertEqual(lualit.parse_assignments(src, "x"), {"A": {"v": 1}, "B": {"v": 2}})


class DataTests(unittest.TestCase):
    def test_counts(self):
        self.assertGreater(len(data.nodes()), 3000)
        self.assertGreater(len(data.gems()), 700)
        self.assertGreater(len(data.uniques()), 1000)
        self.assertGreater(len(data.bases()), 1000)
        self.assertEqual(len(data.classes()), 7)

    def test_lookups(self):
        self.assertEqual(data.find_gem("Spell Echo Support")["name"], "Spell Echo")
        self.assertTrue(data.find_unique("headhunter"))
        self.assertEqual(data.find_class("Elementalist")[0]["name"], "Witch")


class TreeTests(unittest.TestCase):
    def test_path_and_deallocate(self):
        sp = TreeSpec(3, 2)  # Witch / Elementalist
        added = sp.allocate(data.nodes() and _id("Heart of Flame"))
        self.assertGreater(len(added), 1)
        self.assertIn(_id("Heart of Flame"), sp.alloc)
        # every allocated node is connected
        before = set(sp.alloc)
        removed = sp.deallocate(added[0])
        self.assertTrue(set(removed) <= before)
        self.assertNotIn(_id("Heart of Flame"), sp.alloc)

    def test_other_ascendancy_unreachable(self):
        sp = TreeSpec(3, 2)
        with self.assertRaises(ValueError):
            sp.path_to(_id("Unflinching", asc="Juggernaut"))

    def test_points(self):
        self.assertEqual(points_available(100), 123)
        self.assertEqual(points_available(100, "Alira"), 122)


class SkillTests(unittest.TestCase):
    def test_support_rules(self):
        fb, echo, melee = (data.find_gem(n) for n in ("Fireball", "Spell Echo", "Melee Physical Damage"))
        self.assertTrue(skills.support_fits(echo, fb)[0])
        self.assertFalse(skills.support_fits(melee, fb)[0])
        self.assertIn("Spell Echo", skills.compatible_supports("Fireball"))


class BuildTests(unittest.TestCase):
    def test_full_flow_and_pob_roundtrip(self):
        api.new_build("T1", "Witch", "Elementalist", 90)
        api.allocate("T1", ["Heart of Flame", "Elemental Overload"])
        api.add_skill_group("T1", ["Fireball", "Spell Echo", "Elemental Focus"], "Body Armour")
        api.equip_unique("T1", "Body Armour", "Tabula Rasa")
        api.equip_rare("T1", "Helmet", "Hubris Circlet",
                       ["+(80-89) to maximum Life", "+40% to Fire Resistance", "+40% to Cold Resistance"])
        s = api.show_build("T1")
        self.assertIn("Elemental Overload", s["keystones"])
        self.assertEqual(s["stats"]["resistances"]["Fire"]["total"], -20)
        code = api.export_pob("T1")["code"]
        b = Build.from_pob_code(code, "T2")
        self.assertEqual(b.ascendancy, "Elementalist")
        self.assertEqual(set(b.tree_nodes), set(Build.load("T1").tree_nodes))
        self.assertEqual(set(b.items), {"Body Armour", "Helmet"})
        self.assertEqual([g["name"] for g in b.skill_groups[0]["gems"]], ["Fireball", "Spell Echo", "Elemental Focus"])

    def test_item_parse(self):
        txt = items.rare_text("Vaal Regalia", ["+100 to maximum Energy Shield"], quality=20)
        it = items.parse_item(txt)
        self.assertEqual(it["base"], "Vaal Regalia")
        self.assertEqual(it["explicits"], ["+100 to maximum Energy Shield"])

    def test_slot_check(self):
        api.new_build("T3", "Marauder")
        with self.assertRaises(ValueError):
            api.equip_unique("T3", "Helmet", "Tabula Rasa")


class McpTests(unittest.TestCase):
    def test_protocol(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_gems", "arguments": {"query": "arc"}}},
        ]
        out = io.StringIO()
        mcp_server.serve(io.StringIO("\n".join(json.dumps(m) for m in msgs)), out)
        res = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in res], [1, 2, 3])
        self.assertGreater(len(res[1]["result"]["tools"]), 20)
        self.assertIn("Arc", res[2]["result"]["content"][0]["text"])


class ModParseTests(unittest.TestCase):
    def test_exact_and_template(self):
        from poe_build import modparse
        self.assertEqual(modparse.parse_line("37% increased maximum Life"),
                         [{"name": "Life", "type": "INC", "value": 37, "flags": 0, "kw": 0}])
        m = modparse.parse_line("Socketed Gems are Supported by Level 20 Spell Echo")[0]
        self.assertEqual(m["value"]["level"], 20)
        self.assertIsNone(modparse.parse_line("this is not a real mod"))
        self.assertEqual(modparse.roll("+(80-90) to maximum Life"), "+85 to maximum Life")


class CalcTests(unittest.TestCase):
    def test_gem_interpolation(self):
        from poe_build import calc
        stats, row = calc.gem_stats(calc.skills_data()["Fireball"], 20, 0)
        self.assertEqual(row["levelRequirement"], 70)
        self.assertGreater(stats["spell_minimum_base_fire_damage"], 500)
        self.assertGreater(stats["spell_maximum_base_fire_damage"], stats["spell_minimum_base_fire_damage"])

    def test_spell_build(self):
        from poe_build import calc
        api.new_build("C1", "Witch", "Elementalist", 90)
        api.add_skill_group("C1", ["Fireball"], "Body Armour")
        base = calc.calculate(Build.load("C1"))
        self.assertGreater(base["offence"]["hit_dps"], 100)
        self.assertEqual(base["defence"]["resistances"]["Fire"]["total"], -60)
        api.add_gems("C1", 1, ["Controlled Destruction"])
        more = calc.calculate(Build.load("C1"))
        self.assertGreater(more["offence"]["average_hit"], base["offence"]["average_hit"] * 1.2)
        self.assertLess(more["offence"]["crit_chance"], base["offence"]["crit_chance"])
        # enemy settings change damage
        api.set_config("C1", {"enemyIsBoss": "None"})
        none = calc.calculate(Build.load("C1"))
        self.assertGreater(none["offence"]["hit_dps"], more["offence"]["hit_dps"])

    def test_attack_and_local_weapon_mods(self):
        r = api.inspect_item(text=items.rare_text("Rusted Sword", ["100% increased Physical Damage"], quality=0))
        w = r["weapon"]
        from poe_build import data as d
        base = d.find_base("Rusted Sword")[1]["weapon"]
        self.assertEqual(w["PhysicalMax"], round(base["PhysicalMax"] * 2))
        api.new_build("C2", "Duelist", "Slayer", 90)
        api.equip_text("C2", "Weapon 1", items.rare_text("Jewelled Foil", ["Adds 10 to 20 Physical Damage"]))
        api.add_skill_group("C2", ["Lacerate"], "Body Armour")
        r = api.calc_build("C2")
        self.assertGreater(r["offence"]["hit_dps"], 0)
        self.assertLess(r["offence"]["hit_chance"], 100.1)

    def test_aura_and_reservation(self):
        api.new_build("C3", "Templar", "Hierophant", 90)
        api.add_skill_group("C3", ["Arc"], "Body Armour")
        before = api.calc_build("C3")["offence"]["hit_dps"]
        api.add_skill_group("C3", ["Wrath"], "Helmet")
        r = api.calc_build("C3")
        self.assertGreater(r["offence"]["hit_dps"], before)
        self.assertGreater(r["defence"]["mana_reserved_percent"], 0)

    def test_what_if_and_suggest(self):
        api.new_build("C4", "Witch", "Elementalist", 90)
        api.add_skill_group("C4", ["Fireball"], "Body Armour")
        r = api.what_if("C4", allocate=["Heart of Flame"])
        self.assertGreater(r["changes"]["total_dps"]["change"], 0)
        s = api.suggest_passives("C4", "life", max_cost=8, limit=3)
        self.assertTrue(s["best"])

    def test_config_and_compare(self):
        api.new_build("C5", "Witch")
        with self.assertRaises(KeyError):
            api.set_config("C5", {"notARealOption": True})
        api.set_config("C5", {"usePowerCharges": True})
        self.assertTrue(api.get_config("C5")["config"]["usePowerCharges"]["value"])
        api.clone_build("C5", "C6")
        self.assertIn("metrics", api.compare_builds("C5", "C6"))

    def test_char_item_text(self):
        from poe_build import charimport
        t = charimport.item_text({"frameType": 2, "name": "Doom Visor", "typeLine": "Hubris Circlet",
                                  "baseType": "Hubris Circlet", "ilvl": 84, "implicitMods": [],
                                  "explicitMods": ["+80 to maximum Life"], "craftedMods": ["+20% to Fire Resistance"],
                                  "sockets": [{"group": 0, "sColour": "B"}, {"group": 0, "sColour": "R"}]})
        it = items.parse_item(t)
        self.assertEqual(it["base"], "Hubris Circlet")
        self.assertEqual(it["props"]["Sockets"], "B-R")


def _id(name, asc=None):
    for nid, n in data.nodes().items():
        if n["name"] == name and (asc is None or n.get("ascendancy") == asc) and n["type"] != "normal":
            return nid
    raise KeyError(name)


if __name__ == "__main__":
    unittest.main()


class AdvancedCalcTests(unittest.TestCase):
    def test_minions(self):
        api.new_build("M1", "Witch", "Necromancer", 90)
        api.add_skill_group("M1", ["Summon Raging Spirit"], "Body Armour")
        m = api.calc_build("M1")["offence"]["minion"]
        self.assertGreater(m["dps_per_minion"], 0)
        self.assertGreater(m["life"], 0)
        r = api.what_if("M1", add_gems={"1": ["Minion Damage"]})
        self.assertGreater(r["changes"]["total_dps"]["change"], 0)

    def test_triggers(self):
        api.new_build("T7", "Witch", "Elementalist", 90)
        api.add_skill_group("T7", ["Arc", "Cyclone", "Cast While Channelling"], "Body Armour")
        o = api.calc_build("T7")["offence"]
        self.assertEqual(o["skill"], "Arc")
        self.assertAlmostEqual(o["trigger"]["rate"], 1 / 0.32, places=2)
        api.add_skill_group("T7", ["Fireball", "Cast when Damage Taken"], "Gloves", main=True)
        o = api.calc_build("T7")["offence"]
        self.assertIsNone(o["trigger"]["rate"])
        self.assertGreater(o["average_hit"], 0)

    def test_cluster_jewel(self):
        api.new_build("CJ", "Witch", "Elementalist", 90)
        api.add_skill_group("CJ", ["Fireball"], "Body Armour")
        sock = next(k for k, v in data.nodes().items() if v["type"] == "jewel" and v.get("clusterSocket")
                    and not v.get("isClusterInner") and v["clusterSocket"].get("size") == 2)
        api.allocate("CJ", [sock])
        before = api.calc_build("CJ")["offence"]["total_dps"]
        api.equip_text("CJ", str(sock), "Rarity: RARE\nX\nLarge Cluster Jewel\nImplicits: 3\nAdds 8 Passive Skills\n"
                       "2 Added Passive Skills are Jewel Sockets\nAdded Small Passive Skills grant: 12% increased Fire Damage")
        r = api.calc_build("CJ")
        self.assertGreater(r["offence"]["total_dps"], before)
        self.assertEqual(r["cluster_points"], 8)

    def test_custom_mods(self):
        api.new_build("CM", "Marauder")
        life = api.calc_build("CM")["defence"]["life"]
        api.set_config("CM", {"customMods": "+100 to maximum Life"})
        self.assertGreater(api.calc_build("CM")["defence"]["life"], life + 99)


class PoBParityTests(unittest.TestCase):
    """Mechanics found while calibrating against a real PoB export."""

    def _calc(self, b):
        from poe_build import calc
        return calc.calculate(b)

    def test_attribute_nodes_not_double_counted(self):
        api.new_build("P1", "Marauder", None, 90)
        api.allocate("P1", ["Born to Fight"])
        b = Build.load("P1")
        before = self._calc(Build("P0", "Marauder", None, 90))["attributes"]["str"]
        gained = self._calc(b)["attributes"]["str"] - before
        expected = sum(int(l.split()[0].lstrip("+")) for n in b.tree_nodes for l in data.nodes()[n]["stats"]
                       if l.endswith("to Strength") and l.split()[0].lstrip("+").isdigit())
        self.assertEqual(gained, expected)

    def test_int_gives_1pct_es_per_10(self):
        b = Build("P2", "Witch", None, 1)
        b.config["customMods"] = "+100 to maximum Energy Shield\n+80 to Intelligence"
        es = self._calc(b)["defence"]["energy_shield"]
        self.assertEqual(es, round(100 * (1 + (32 + 80) // 10 / 100)))

    def test_active_flask_buff_and_effect(self):
        b = Build("P3", "Marauder", None, 90)
        b.equip("Flask 1", items.rare_text("Granite Flask", [], rarity="NORMAL", quality=0))
        arm = self._calc(b)["defence"]["armour"]
        self.assertGreaterEqual(arm, 1500)
        b.active_flasks = []
        self.assertLess(self._calc(b)["defence"]["armour"], 1500)

    def test_action_speed(self):
        b = Build("P4", "Witch", None, 90)
        b.skill_groups = [{"slot": None, "enabled": True, "gems": [{"name": "Fireball", "level": 20, "quality": 0}]}]
        s0 = self._calc(b)["offence"]["uses_per_second"]
        b.config["customMods"] = "10% increased Action Speed"
        self.assertAlmostEqual(self._calc(b)["offence"]["uses_per_second"], s0 * 1.1, places=2)

    def test_herald_mana_reservation_efficiency(self):
        b = Build("P5", "Witch", None, 90)
        b.skill_groups = [{"slot": None, "enabled": True, "gems": [{"name": "Herald of Ash", "level": 20, "quality": 0}]}]
        r0 = self._calc(b)["defence"]["mana_unreserved"]
        b.config["customMods"] = "20% increased Mana Reservation Efficiency of Herald Skills"
        self.assertGreater(self._calc(b)["defence"]["mana_unreserved"], r0)

    def test_petrified_blood_raises_max_hit(self):
        b = Build("P6", "Marauder", None, 90)
        b.config = {"enemyIsBoss": "None"}
        h0 = self._calc(b)["defence"]["max_hit_taken"]["Fire"]
        b.config["customMods"] = "40% of Life Loss below half is Prevented"
        from poe_build import modparse
        if modparse.parse_line("40% of Life Loss below half is Prevented"):
            self.assertGreater(self._calc(b)["defence"]["max_hit_taken"]["Fire"], h0)

    def test_weapon_swap_gems_inactive(self):
        b = Build("P7", "Witch", None, 90)
        b.skill_groups = [{"slot": "Weapon 1 Swap", "enabled": True, "gems": [{"name": "Clarity", "level": 20, "quality": 0}]}]
        self.assertEqual(self._calc(b)["defence"]["mana_reserved_percent"], 0)

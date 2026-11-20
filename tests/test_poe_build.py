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


def _id(name, asc=None):
    for nid, n in data.nodes().items():
        if n["name"] == name and (asc is None or n.get("ascendancy") == asc) and n["type"] != "normal":
            return nid
    raise KeyError(name)


if __name__ == "__main__":
    unittest.main()

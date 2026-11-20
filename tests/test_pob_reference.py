"""Reference values taken from Path of Building Community's own spec tests
(spec/System/TestDefence_spec.lua). A fresh PoB build is a level 1 Scion."""
import os
import tempfile
import unittest

os.environ.setdefault("POE_BUILDS_DIR", tempfile.mkdtemp())

from poe_build import calc  # noqa: E402
from poe_build.build import Build  # noqa: E402


def maxhits(custom="", **cfg):
    b = Build("ref", "Scion", None, 1)
    b.config = {"enemyIsBoss": "None", **cfg}
    if custom:
        b.config["customMods"] = custom
    d = calc.calculate(b)["defence"]["max_hit_taken"]
    return [d[k] for k in ("Physical", "Fire", "Cold", "Lightning", "Chaos")]


class PoBMaxHitReference(unittest.TestCase):
    """spec/System/TestDefence_spec.lua: 'no armour max hits'"""

    def test_base(self):
        self.assertEqual(maxhits(), [60, 38, 38, 38, 38])

    def test_resists_and_pdr(self):
        self.assertEqual(maxhits("+200 to all resistances\n200% additional Physical Damage Reduction"),
                         [600, 240, 240, 240, 240])

    def test_overwhelm(self):
        self.assertEqual(maxhits("+200 to all resistances\n+200 to all maximum resistances\n"
                                 "200% additional Physical Damage Reduction", enemyPhysicalOverwhelm=15),
                         [240, 600, 600, 600, 600])

    def test_reduced_taken(self):
        self.assertEqual(maxhits("+200 to all resistances\n+200 to all maximum resistances\n50% reduced damage taken"),
                         [120, 1200, 1200, 1200, 1200])

    def test_less_taken(self):
        self.assertEqual(maxhits("+200 to all resistances\n+200 to all maximum resistances\n"
                                 "50% reduced damage taken\n50% less damage taken"),
                         [240, 2400, 2400, 2400, 2400])

    def test_enemy_less_damage(self):
        self.assertEqual(maxhits("+200 to all resistances\n+200 to all maximum resistances\n"
                                 "50% reduced damage taken\n50% less damage taken\nNearby enemies deal 20% less damage"),
                         [300, 3000, 3000, 3000, 3000])


if __name__ == "__main__":
    unittest.main()


def main_skill_level(items=None, gems="Fireball 20/0", slot=None, custom=""):
    b = Build("ref", "Scion", None, 90)
    for sl, text in (items or {}).items():
        b.items[sl] = text
    group = []
    for g in gems.split("\n"):
        name, lq = g.rsplit(" ", 1)
        lv, q = lq.split("/")
        group.append({"name": name, "level": int(lv), "quality": int(q), "enabled": True})
    b.skill_groups = [{"slot": slot, "gems": group, "enabled": True}]
    if custom:
        b.config["customMods"] = custom
    c = calc.Calculator(b)
    c.setup()
    sk = c.build_skill(b.skill_groups[0], 0)
    return sk["level"], sk["quality"]


class PoBGemLevelReference(unittest.TestCase):
    """spec/System/TestSkills_spec.lua"""

    def test_level_cap(self):
        self.assertEqual(main_skill_level(custom="+30 to Level of all Spell Skill Gems")[0], 40)

    def test_negative(self):
        helm = "Rarity: RARE\nTest Helmet\nHubris Circlet\nSockets: G\nImplicits: 0\n-5 to Level of Socketed Gems\n-10% to Quality of Socketed Gems"
        lv, q = main_skill_level({"Helmet": helm}, "Fireball 20/20", "Helmet",
                                 "-1 to Level of all Spell Skill Gems\n-2% to Quality of all Skill Gems")
        self.assertEqual((lv, q), (14, 8))

    def test_items_globals_and_empower(self):
        robe = ("Rarity: RARE\nTest Robe\nSage's Robe\nSockets: B-R-G\nImplicits: 0\n+2 to Level of Socketed Fire Gems\n"
                "+3 to Level of Socketed Projectile Gems\n+10% to Quality of Socketed Gems\n+4% to Quality of Socketed Fire Gems")
        lv, q = main_skill_level({"Body Armour": robe}, "Fireball 20/5\nEmpower 3/0\nEnhance 3/0", "Body Armour",
                                 "+1 to Level of all Spell Skill Gems\n+2 to Level of all Fire Skill Gems\n"
                                 "+5% to Quality of all Skill Gems\n+3% to Quality of all Fire Skill Gems")
        self.assertEqual(lv, 30)
        # PoB also adds +10% "socket colour" quality (53); socket colours are not modelled here
        self.assertEqual(q, 43)


class PoBAttackReference(unittest.TestCase):
    """spec/System/TestAttacks_spec.lua (default attack, level 1 Scion)"""

    def _off(self, weapon=None):
        b = Build("ref", "Scion", None, 1)
        if weapon:
            b.items["Weapon 1"] = weapon
        return calc.calculate(b)["offence"]

    def test_unarmed(self):
        o = self._off()
        self.assertEqual(o["skill"], "Default Attack")
        self.assertEqual(o["crit_chance"], 0)
        self.assertEqual(o["crit_multiplier"], 150)

    def test_bow_crit_chance(self):
        o = self._off("New Item\nMaraketh Bow\nQuality: 20\nImplicits: 1\n{tags:speed}10% increased Movement Speed")
        self.assertEqual(o["crit_chance"], 5.5)

    def test_crit_multi_implicit(self):
        o = self._off("New Item\nAssassin Bow\nQuality: 20\nImplicits: 1\n"
                      "{tags:damage,critical}{range:0.5}+(15-25)% to Global Critical Strike Multiplier")
        self.assertEqual(o["crit_multiplier"], 170)

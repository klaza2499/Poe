"""Approximate character stats from tree + items.

This is NOT Path of Building's full calculation engine (no DPS, no conditional
mods, no buffs). It gives the numbers a build planner needs to sanity-check a
build quickly: attributes, life/mana/ES, resistances, requirements and a summed
list of every stat line. For exact DPS/EHP export the build to PoB (export_pob).
"""
from __future__ import annotations

import re
from collections import OrderedDict

from . import data, items as itemsmod

NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")
RES_PENALTY = -60  # after Act 10 Kitava (PoE 1)


def roll(line: str, at=0.5) -> str:
    """Replace (a-b) ranges by a roll value (default midpoint)."""
    def f(m):
        a, b = float(m.group(1)), float(m.group(2))
        v = a + (b - a) * at
        return str(int(round(v))) if a.is_integer() and b.is_integer() else f"{v:g}"
    return RANGE.sub(f, line)


UNUM = re.compile(r"\d+(?:\.\d+)?")


def template(line: str):
    """'+12% to Fire Resistance' -> ('+#% to Fire Resistance', [12]); signs stay in the template."""
    nums = [float(x) for x in UNUM.findall(line)]
    return UNUM.sub("#", line), nums


def sum_templates(lines):
    acc = OrderedDict()
    for l in lines:
        t, nums = template(l)
        if t not in acc:
            acc[t] = [0.0] * len(nums)
            acc[t + "\x00count"] = 0
        if len(acc[t]) == len(nums):
            acc[t] = [a + b for a, b in zip(acc[t], nums)]
        acc[t + "\x00count"] += 1
    out = []
    for t, v in acc.items():
        if t.endswith("\x00count"):
            continue
        s = t
        for n in v:
            s = s.replace("#", f"{n:g}", 1)
        c = acc[t + "\x00count"]
        out.append(s if c == 1 else f"{s}  (x{c} sources)")
    return out


def _find(lines, pattern, group=1):
    tot = 0.0
    rx = re.compile(pattern, re.I)
    for l in lines:
        m = rx.search(l)
        if m:
            tot += float(m.group(group))
    return tot


def _item_defences(text):
    """Local armour/evasion/ES/ward of an armour piece (base x local increases + quality)."""
    it = itemsmod.parse_item(text)
    fb = data.find_base(it["base"])
    out = {"armour": 0, "evasion": 0, "es": 0, "ward": 0}
    if not fb or not fb[1].get("armour"):
        return out, []
    a = fb[1]["armour"]
    lines = [roll(l) for l in itemsmod.active_mod_lines(it)]
    q = float(re.sub(r"[^\d.]", "", it["props"].get("Quality", "0")) or 0)
    used = []

    def calc(key, base_min, base_max, word):
        base = (a.get(base_min, 0) + a.get(base_max, a.get(base_min, 0))) / 2
        if not base:
            return 0
        flat = inc = 0.0
        for l in lines:
            m = re.match(rf"^\+?(\d+(?:\.\d+)?) to {word}$", l, re.I)
            if m:
                flat += float(m.group(1)); used.append(l)
            m = re.match(rf"^(\d+(?:\.\d+)?)% increased {word}$", l, re.I)
            if m:
                inc += float(m.group(1)); used.append(l)
            for combo in (rf"^(\d+)% increased {word} and \w[\w ]*$", rf"^(\d+)% increased \w[\w ]* and {word}$",
                          r"^(\d+)% increased Armour, Evasion and Energy Shield$"):
                m = re.match(combo, l, re.I)
                if m and (word.split()[0].lower() in l.lower() or "Armour, Evasion" in l):
                    inc += float(m.group(1)); used.append(l)
        return round((base + flat) * (1 + (inc + q) / 100))

    out["armour"] = calc("armour", "ArmourBaseMin", "ArmourBaseMax", "Armour")
    out["evasion"] = calc("evasion", "EvasionBaseMin", "EvasionBaseMax", "Evasion Rating")
    out["es"] = calc("es", "EnergyShieldBaseMin", "EnergyShieldBaseMax", "maximum Energy Shield")
    out["ward"] = calc("ward", "WardBaseMin", "WardBaseMax", "Ward")
    return out, used


def compute(build) -> dict:
    """build: poe_build.build.Build"""
    spec = build.spec()
    cls = spec.cls
    level = build.level
    tree_lines = [s for _, s in spec.stat_lines()]
    item_lines, local_used, defences = [], [], {"armour": 0, "evasion": 0, "es": 0, "ward": 0}
    item_reqs = []
    for slot, text in build.items.items():
        if "Swap" in slot:
            continue
        it = itemsmod.parse_item(text)
        d, used = _item_defences(text)
        for k in defences:
            defences[k] += d[k]
        lines = [roll(l) for l in itemsmod.active_mod_lines(it)]
        used_set = list(used)
        for l in lines:
            if l in used_set:
                used_set.remove(l)
                continue
            # local weapon lines don't count globally
            if itemsmod.item_type(text) in itemsmod.WEAPON_TYPES and re.search(
                    r"(Adds .* Damage$|increased Physical Damage$|increased Attack Speed$|increased Critical Strike Chance$)", l):
                continue
            item_lines.append(l)
        fb = data.find_base(it["base"])
        if fb:
            item_reqs.append((slot, it["name"] or it["base"], fb[1].get("req", {})))
    for nid, text in build.jewels.items():
        it = itemsmod.parse_item(text)
        item_lines += [roll(l) for l in itemsmod.active_mod_lines(it)]
    lines = tree_lines + item_lines
    keystones = spec.keystones()

    # attributes
    gs, gd, gi = spec.granted_attributes()
    allattr = _find(lines, r"^\+(\d+) to all Attributes$")

    def attr(name, base, granted):
        flat = base + granted + allattr + _find(lines, rf"^\+(\d+) to {name}$")
        for other in ("Strength", "Dexterity", "Intelligence"):
            if other == name:
                continue
            flat += _find(lines, rf"^\+(\d+) to {name} and {other}$") + _find(lines, rf"^\+(\d+) to {other} and {name}$")
        inc = _find(lines, rf"^(\d+)% increased {name}$") + _find(lines, r"^(\d+)% increased Attributes$")
        return int(flat * (1 + inc / 100))

    s = attr("Strength", cls["str"], gs)
    d = attr("Dexterity", cls["dex"], gd)
    i = attr("Intelligence", cls["int"], gi)

    # life / mana / es
    life_flat = _find(lines, r"^\+(\d+) to maximum Life$")
    life_inc = _find(lines, r"^(\d+)% increased maximum Life$") - _find(lines, r"^(\d+)% reduced maximum Life$")
    life = (38 + 12 * level + s // 2 + life_flat) * (1 + life_inc / 100)
    mana_flat = _find(lines, r"^\+(\d+) to maximum Mana$")
    mana_inc = _find(lines, r"^(\d+)% increased maximum Mana$") - _find(lines, r"^(\d+)% reduced maximum Mana$")
    mana = (34 + 6 * level + i // 2 + mana_flat) * (1 + mana_inc / 100)
    es_flat = _find(lines, r"^\+(\d+) to maximum Energy Shield$") + defences["es"]
    es_inc = _find(lines, r"^(\d+)% increased maximum Energy Shield$") + i // 5
    es = es_flat * (1 + es_inc / 100)
    arm = (defences["armour"] + _find(lines, r"^\+(\d+) to Armour$")) * (
        1 + (_find(lines, r"^(\d+)% increased Armour$") + _find(lines, r"^(\d+)% increased Armour and Evasion")
             + _find(lines, r"^(\d+)% increased Evasion Rating and Armour")) / 100)
    eva = (defences["evasion"] + 15 + 2 * level + _find(lines, r"^\+(\d+) to Evasion Rating$")) * (
        1 + (_find(lines, r"^(\d+)% increased Evasion Rating$") + d // 5
             + _find(lines, r"^(\d+)% increased Armour and Evasion") + _find(lines, r"^(\d+)% increased Evasion Rating and Armour")) / 100)
    if "Chaos Inoculation" in keystones:
        life = 1
    if "Eldritch Battery" in keystones:
        mana, es = mana + es, 0

    # resistances
    res = {}
    for el in ("Fire", "Cold", "Lightning", "Chaos"):
        v = _find(lines, rf"^\+?(-?\d+)% to {el} Resistance$")
        if el != "Chaos":
            v += _find(lines, r"^\+?(-?\d+)% to all Elemental Resistances$")
            for other in ("Fire", "Cold", "Lightning"):
                if other != el:
                    v += _find(lines, rf"^\+?(\d+)% to {el} and {other} Resistances$")
                    v += _find(lines, rf"^\+?(\d+)% to {other} and {el} Resistances$")
        v += _find(lines, rf"^\+?(\d+)% to {el} and Chaos Resistances$") if el != "Chaos" else sum(
            _find(lines, rf"^\+?(\d+)% to {o} and Chaos Resistances$") for o in ("Fire", "Cold", "Lightning"))
        cap = 75 + _find(lines, rf"^\+(\d+)% to maximum {el} Resistance$") + (
            _find(lines, r"^\+(\d+)% to all maximum Elemental Resistances$") if el != "Chaos" else 0) + (
            _find(lines, r"^\+(\d+)% to all maximum Resistances$"))
        total = v + RES_PENALTY
        if el == "Chaos" and "Chaos Inoculation" in keystones:
            total = 100
        res[el] = {"total": int(total), "max": int(cap), "effective": int(min(total, cap)),
                   "overcap": int(max(0, total - cap)), "missing_to_cap": int(max(0, cap - total))}

    # requirements
    problems = []
    for slot, name, req in item_reqs:
        for k, have in (("str", s), ("dex", d), ("int", i)):
            if req.get(k, 0) > have:
                problems.append(f"{slot} ({name}) needs {req[k]} {k.upper()}, you have {have}")
        if req.get("level", 0) > level:
            problems.append(f"{slot} ({name}) needs level {req['level']}")

    pts = spec.counts()
    from .tree import points_available
    avail = points_available(level, build.bandit)
    if pts["passives"] > avail:
        problems.append(f"{pts['passives']} passive points used but only {avail} available at level {level}")
    if pts["ascendancy"] > 8:
        problems.append(f"{pts['ascendancy']} ascendancy points used (max 8)")
    for el in ("Fire", "Cold", "Lightning"):
        if res[el]["missing_to_cap"]:
            problems.append(f"{el} resistance {res[el]['total']}% (uncapped, needs +{res[el]['missing_to_cap']}%)")

    return {
        "approximate": True,
        "note": "Quick estimate (no conditionals/buffs/DPS). Export to Path of Building for exact numbers.",
        "level": level,
        "attributes": {"str": s, "dex": d, "int": i},
        "life": int(life), "mana": int(mana), "energy_shield": int(es),
        "armour": int(arm), "evasion": int(eva), "ward": defences["ward"],
        "resistances": res,
        "points": {**pts, "passives_available": avail, "ascendancy_available": 8},
        "keystones": keystones,
        "problems": problems,
        "stat_totals": sum_templates(lines),
    }

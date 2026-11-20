"""Build stats: wraps the calc engine (poe_build.calc) and adds requirement / point checks
plus a summed list of every stat line for reading."""
from __future__ import annotations

import math
import re
from collections import OrderedDict

from . import data, items as itemsmod

RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")


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


def gem_requirement(level_req, is_support, multi):
    """In-game gem attribute requirement formula (PoB calcLib.getGemStatRequirement)."""
    if not multi:
        return 0
    st = 0.5 if is_support else 0.7
    req = math.floor((20 + (level_req - 3) * 3) * (multi / 100) ** 0.9 * st + 0.5)
    return 0 if req < 14 else req


def compute(build) -> dict:
    """Stats for a build using the calc engine (poe_build.calc) + requirement / point checks."""
    from . import calc
    from .tree import points_available
    res = calc.calculate(build)
    d, a = res["defence"], res["attributes"]
    s, dx, i = a["str"] or 0, a["dex"] or 0, a["int"] or 0
    level = build.level
    problems = []
    # item requirements
    for slot, text in build.items.items():
        if "Swap" in slot:
            continue
        it = itemsmod.parse_item(text)
        fb = data.find_base(it["base"])
        req = dict(fb[1].get("req", {})) if fb else {}
        if it["props"].get("Requires Level"):
            req["level"] = int(it["props"]["Requires Level"])
        for k, have in (("str", s), ("dex", dx), ("int", i)):
            if req.get(k, 0) > have:
                problems.append(f"{slot} ({it['name'] or it['base']}) needs {req[k]} {k.upper()}, you have {have}")
        if req.get("level", 0) > level:
            problems.append(f"{slot} ({it['name'] or it['base']}) needs level {req['level']}")
    # gem requirements
    for gi, g in enumerate(build.skill_groups, 1):
        for gem in g.get("gems", []):
            gd = data.find_gem(gem["name"])
            if not gd:
                continue
            sk = calc.skills_data().get(gd.get("skillId"), {})
            row = (sk.get("levels") or {}).get(str(gem.get("level", 20))) or {}
            lr = row.get("levelRequirement", 1)
            for k, have in (("str", s), ("dex", dx), ("int", i)):
                need = gem_requirement(lr, gd["support"], gd["req"].get(k, 0))
                if need > have:
                    problems.append(f"gem {gem['name']} lv{gem.get('level', 20)} needs {need} {k.upper()}, you have {have}")
            if lr > level:
                problems.append(f"gem {gem['name']} lv{gem.get('level', 20)} needs character level {lr}")
    spec = build.spec()
    pts = spec.counts()
    avail = points_available(level, build.bandit)
    used = pts["passives"] + res.get("cluster_points", 0)
    if used > avail:
        extra = f" (incl. {res['cluster_points']} on cluster jewels)" if res.get("cluster_points") else ""
        problems.append(f"{used} passive points used{extra} but only {avail} available at level {level}")
    if pts["ascendancy"] > 8:
        problems.append(f"{pts['ascendancy']} ascendancy points used (max 8)")
    for el in ("Fire", "Cold", "Lightning"):
        r = d["resistances"][el]
        if (r["total"] or 0) < (r["max"] or 75):
            problems.append(f"{el} resistance {r['total']:.0f}% (uncapped, needs +{(r['max'] or 75) - (r['total'] or 0):.0f}%)")
    if (d.get("mana_unreserved") or 0) < 0:
        problems.append(f"mana reserved exceeds maximum mana ({d['mana_reserved_percent']}%)")
    off = res["offence"]
    if off.get("cost", {}).get("Mana") and d.get("mana_unreserved") is not None and off["cost"]["Mana"] > d["mana_unreserved"]:
        problems.append(f"main skill costs {off['cost']['Mana']} mana but only {d['mana_unreserved']} unreserved")
    # summed stat lines (all sources) for reading
    lines = [ln for _, ln in spec.stat_lines()]
    for slot, text in build.items.items():
        if "Swap" not in slot:
            lines += [roll(x) for x in itemsmod.active_mod_lines(itemsmod.parse_item(text))]
    for text in build.jewels.values():
        lines += [roll(x) for x in itemsmod.active_mod_lines(itemsmod.parse_item(text))]
    return {
        "note": "Calculated by poe_build's PoB-data engine. Very close to PoB for common builds; export to PoB to double-check.",
        "level": level,
        "attributes": a,
        "life": d["life"], "mana": d["mana"], "energy_shield": d["energy_shield"], "armour": d["armour"],
        "evasion": d["evasion"], "ward": d["ward"],
        "resistances": {el: {"total": r["total"], "max": r["max"], "effective": r["effective"],
                             "overcap": r["overcap"], "missing_to_cap": max(0, (r["max"] or 0) - (r["total"] or 0))}
                        for el, r in d["resistances"].items()},
        "defence": d,
        "offence": off,
        "buffs": res["buffs"], "notes": res["notes"],
        "unparsed_mods": res["unparsed_mods"], "unparsed_count": res["unparsed_count"],
        "points": {**pts, "passives_available": avail, "ascendancy_available": 8},
        "keystones": spec.keystones(),
        "problems": problems,
        "stat_totals": sum_templates(lines),
    }

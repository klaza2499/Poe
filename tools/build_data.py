"""Convert Path of Building Community's Lua game data into compact JSON for poe_build.

Usage (from repo root):
    git clone --depth 1 --filter=blob:none --sparse https://github.com/PathOfBuildingCommunity/PathOfBuilding.git pob
    (cd pob && git sparse-checkout set --no-cone src/Data src/TreeData/3_30/tree.lua)
    python -I tools/build_data.py pob/src [--tree 3_30]

Lua files are only *parsed* (tools/lualit.py), never executed.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lualit  # noqa: E402
import luaexpr  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "poe_build", "data")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def dump(name, obj):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name + ".json.gz")
    data = json.dumps(lualit.plain(obj), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(p, "wb") as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0))
    print(f"  {name}: {len(data)/1e6:.2f} MB raw -> {os.path.getsize(p)/1e6:.2f} MB gz")


def clean_type(x):
    """'SkillType.Spell' -> 'Spell'."""
    return str(x).split(".")[-1]


# ---------------------------------------------------------------- tree
ORBIT16 = [0, 30, 45, 60, 90, 120, 135, 150, 180, 210, 225, 240, 270, 300, 315, 330]
ORBIT40 = [0, 10, 20, 30, 40, 45, 50, 60, 70, 80, 90, 100, 110, 120, 130, 135, 140, 150, 160, 170, 180,
           190, 200, 210, 220, 225, 230, 240, 250, 260, 270, 280, 290, 300, 310, 315, 320, 330, 340, 350]


def orbit_angle(n_in_orbit, idx):
    if n_in_orbit == 16:
        return math.radians(ORBIT16[idx])
    if n_in_orbit == 40:
        return math.radians(ORBIT40[idx])
    return 2 * math.pi * idx / n_in_orbit


def build_tree(src, ver):
    t = lualit.parse_return_table(read(os.path.join(src, "TreeData", ver, "tree.lua")))
    radii = t["constants"]["orbitRadii"]
    per = t["constants"]["skillsPerOrbit"]
    groups = t["groups"]
    nodes = {}
    for nid, n in t["nodes"].items():
        try:
            nid = int(nid)
        except (TypeError, ValueError):
            continue  # "root"
        g = groups.get(n.get("group")) if "group" in n else None
        x = y = None
        if g is not None:
            r = radii[n["orbit"]]
            a = orbit_angle(per[n["orbit"]], n["orbitIndex"])
            x = round(g["x"] + math.sin(a) * r, 1)
            y = round(g["y"] - math.cos(a) * r, 1)
        if n.get("classStartIndex") is not None:
            kind = "classStart"
        elif n.get("isAscendancyStart"):
            kind = "ascendancyStart"
        elif n.get("isKeystone"):
            kind = "keystone"
        elif n.get("isMastery"):
            kind = "mastery"
        elif n.get("isJewelSocket"):
            kind = "jewel"
        elif n.get("isNotable"):
            kind = "notable"
        else:
            kind = "normal"
        e = {"name": n["name"], "type": kind, "stats": n.get("stats", [])}
        if x is not None:
            e["x"], e["y"] = x, y
            e["group"] = n["group"]
        for k_src, k_dst in (("ascendancyName", "ascendancy"), ("reminderText", "reminder"),
                             ("classStartIndex", "classStart"), ("grantedPassivePoints", "grantedPoints"),
                             ("grantedStrength", "grantedStr"), ("grantedDexterity", "grantedDex"),
                             ("grantedIntelligence", "grantedInt"), ("recipe", "anointRecipe"),
                             ("flavourText", "flavour")):
            if n.get(k_src) is not None:
                e[k_dst] = n[k_src]
        for flag in ("isProxy", "isBloodline", "isBlighted", "isMultipleChoice", "isMultipleChoiceOption"):
            if n.get(flag):
                e[flag] = True
        if n.get("expansionJewel"):
            e["clusterSocket"] = n["expansionJewel"]
            if "parent" in n["expansionJewel"]:
                e["isClusterInner"] = True
        if g is not None and g.get("isProxy"):
            e["isProxy"] = True
        if n.get("masteryEffects"):
            e["masteryEffects"] = [{"effect": m["effect"], "stats": m["stats"]} for m in n["masteryEffects"]]
        e["out"] = [int(o) for o in n.get("out", [])]
        e["in"] = [int(o) for o in n.get("in", [])]
        nodes[nid] = e
    # undirected adjacency
    for nid, e in nodes.items():
        adj = set(e.pop("out")) | set(e.pop("in"))
        e["adj"] = sorted(a for a in adj if a in nodes)
    classes = []
    for i, c in enumerate(t["classes"]):
        start = next((nid for nid, e in nodes.items() if e.get("classStart") == i), None)
        ascs = []
        for j, a in enumerate(c.get("ascendancies", []), 1):
            astart = next((nid for nid, e in nodes.items() if e["type"] == "ascendancyStart" and e.get("ascendancy") == a["id"]), None)
            ascs.append({"id": a["id"], "name": a["name"], "ascendClassId": j, "startNode": astart,
                         "flavour": a.get("flavourText")})
        classes.append({"classId": i, "name": c["name"], "str": c["base_str"], "dex": c["base_dex"],
                        "int": c["base_int"], "startNode": start, "ascendancies": ascs})
    alt = []
    for a in t.get("alternate_ascendancies", []):
        astart = next((nid for nid, e in nodes.items() if e["type"] == "ascendancyStart" and e.get("ascendancy") == a["id"]), None)
        alt.append({"id": a["id"], "name": a["name"], "startNode": astart, "flavour": a.get("flavourText")})
    out = {"version": ver, "points": t["points"], "classes": classes, "bloodlines": alt,
           "bounds": [t["min_x"], t["min_y"], t["max_x"], t["max_y"]],
           "jewelSlots": t["jewelSlots"], "nodes": nodes}
    dump("tree", out)
    return out


# ---------------------------------------------------------------- gems / skills
def build_gems(src):
    gems = lualit.parse_return_table(read(os.path.join(src, "Data", "Gems.lua")))
    skills = {}
    for fn in sorted(os.listdir(os.path.join(src, "Data", "Skills"))):
        skills.update(lualit.parse_assignments(read(os.path.join(src, "Data", "Skills", fn)), "skills"))
    out = {}
    for gid, g in gems.items():
        sk = skills.get(g.get("grantedEffectId"), {})
        e = {
            "name": g["name"], "gemId": g.get("gameId"), "variantId": g.get("variantId"),
            "skillId": g.get("grantedEffectId"), "tags": g.get("tagString", ""),
            "tagList": sorted(k for k, v in (g.get("tags") or {}).items() if v),
            "req": {"str": g.get("reqStr", 0), "dex": g.get("reqDex", 0), "int": g.get("reqInt", 0)},
            "maxLevel": g.get("naturalMaxLevel", 20),
            "support": bool(sk.get("support")),
        }
        if g.get("vaalGem"):
            e["vaal"] = True
        if g.get("secondaryGrantedEffectId"):
            e["secondarySkillId"] = g["secondaryGrantedEffectId"]
        if sk.get("description"):
            e["description"] = sk["description"]
        if sk.get("castTime"):
            e["castTime"] = sk["castTime"]
        st = sk.get("skillTypes")
        if isinstance(st, dict):
            e["skillTypes"] = sorted(clean_type(k) for k, v in st.items() if v)
        for k in ("requireSkillTypes", "addSkillTypes", "excludeSkillTypes"):
            if sk.get(k):
                e[k] = [clean_type(x) for x in sk[k]]
        if isinstance(sk.get("baseFlags"), dict):
            e["flags"] = sorted(k for k, v in sk["baseFlags"].items() if v)
        if sk.get("qualityStats"):
            q = sk["qualityStats"]
            if isinstance(q, dict):  # alternate quality layout
                q = q.get("Default", list(q.values())[0] if q else [])
            e["qualityStats"] = [list(x) if isinstance(x, list) else x for x in q]
        if sk.get("constantStats"):
            e["constantStats"] = sk["constantStats"]
        stats = sk.get("stats") or []
        levels = sk.get("levels") or {}
        if isinstance(levels, list):
            levels = {i: v for i, v in enumerate(levels, 1)}
        lv = {}
        for L, row in levels.items():
            try:
                L = int(L)
            except (TypeError, ValueError):
                continue
            if isinstance(row, list):
                row = {i: v for i, v in enumerate(row, 1)}
            r = {"reqLevel": row.get("levelRequirement")}
            vals = [row[i] for i in range(1, len(stats) + 1) if i in row]
            if vals:
                r["stats"] = dict(zip(stats, vals))
            for k in ("cost", "critChance", "damageEffectiveness", "manaMultiplier", "manaReservationPercent",
                      "manaReservationFlat", "cooldown", "attackSpeedMultiplier", "baseMultiplier", "storedUses",
                      "duration", "reservationMultiplier"):
                if row.get(k) is not None:
                    r[k] = row[k]
            lv[L] = r
        if lv:
            keep = sorted(set(k for k in lv if k in (1, 10, 20, 21, e["maxLevel"])) | {max(lv)})
            e["levels"] = {str(k): lv[k] for k in keep}
        if sk.get("minionList"):
            e["minions"] = sk["minionList"]
        out[gid] = e
    dump("gems", out)
    return out


# ---------------------------------------------------------------- uniques
_HEADER = re.compile(r"^(Variant|Source|League|Requires Level|LevelReq|Implicits|Sockets|Radius|Limited to|"
                     r"Selected Variant|Has Alt Variant|Has Alt Variant Two|Upgrade|Item Level|Quality|"
                     r"Catalyst|Talisman Tier|Cluster Jewel Skill|Cluster Jewel Node Count|Crafted|"
                     r"Unreleased|Prefix|Suffix|Influence|Corrupted|Mirrored|Shaper Item|Elder Item|"
                     r"Foulborn|Allocated|Requires|Evasion|Armour|Energy Shield|Ward)\b:?")


def parse_unique(raw):
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    name, base = lines[0], lines[1]
    variants, info, mods, implicits_n = [], {}, [], 0
    for l in lines[2:]:
        m = re.match(r"^([A-Za-z ]+): ?(.*)$", l)
        if m and not l.startswith("{") and m.group(1) in (
                "Variant", "Source", "League", "Requires Level", "LevelReq", "Implicits", "Sockets",
                "Radius", "Limited to", "Upgrade", "Has Alt Variant", "Has Alt Variant Two",
                "Has Alt Variant Three", "Selected Variant", "Item Level", "Talisman Tier"):
            k, v = m.group(1), m.group(2)
            if k == "Variant":
                variants.append(v)
            elif k == "Implicits":
                implicits_n = int(v)
            else:
                info[k] = v
            continue
        if re.match(r"^(Requires Level \d+|Corrupted|Mirrored|Elder Item|Shaper Item|Unreleased)", l) and not l.startswith("{"):
            info.setdefault("flags", []).append(l)
            continue
        vm = re.match(r"^((?:\{[^}]*\})*)(.*)$", l)
        tags_part, text = vm.group(1), vm.group(2)
        var = None
        mtags = []
        for tg in re.findall(r"\{([^}]*)\}", tags_part):
            if tg.startswith("variant:"):
                var = [int(x) for x in tg[8:].split(",") if x.strip().isdigit()]
            elif tg.startswith("tags:"):
                mtags = tg[5:].split(",")
            elif tg == "crafted":
                mtags.append("crafted")
            elif tg.startswith("range:"):
                pass
        mods.append({"text": text, **({"variants": var} if var else {}), **({"tags": mtags} if mtags else {})})
    for i, md in enumerate(mods):
        md["implicit"] = i < implicits_n
    cur = len(variants) if variants else None
    current = [m["text"] for m in mods if not m.get("variants") or (cur in m["variants"])]
    cur_impl = [m["text"] for m in mods if m["implicit"] and (not m.get("variants") or cur in m["variants"])]
    cur_expl = [m["text"] for m in mods if not m["implicit"] and (not m.get("variants") or cur in m["variants"])]
    return {"name": name, "base": base, "variants": variants, "currentVariant": cur, "info": info,
            "implicits": cur_impl, "explicits": cur_expl, "allMods": mods, "raw": raw.strip("\n")}


def build_uniques(src):
    out = []
    udir = os.path.join(src, "Data", "Uniques")
    for fn in sorted(os.listdir(udir)):
        if not fn.endswith(".lua"):
            continue
        cat = fn[:-4]
        t = lualit.parse_return_table(read(os.path.join(udir, fn)))
        for raw in t:
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                u = parse_unique(raw)
            except Exception as ex:  # noqa
                print("  skip unique:", raw[:40].replace("\n", " "), ex)
                continue
            u["category"] = cat
            out.append(u)
    dump("uniques", out)
    return out


# ---------------------------------------------------------------- bases
def build_bases(src):
    out = {}
    bdir = os.path.join(src, "Data", "Bases")
    for fn in sorted(os.listdir(bdir)):
        items = lualit.parse_assignments(read(os.path.join(bdir, fn)), "itemBases")
        for name, b in items.items():
            e = {"type": b.get("type"), "subType": b.get("subType"),
                 "tags": sorted(k for k, v in (b.get("tags") or {}).items() if v),
                 "implicit": b.get("implicit"), "req": b.get("req") or {}}
            for k in ("weapon", "armour", "flask", "tincture", "socketLimit", "hidden", "influenceTags", "implicitIds"):
                if b.get(k) is not None:
                    e[k] = b[k]
            if isinstance(e["req"], list):
                e["req"] = {}
            out[name] = {k: v for k, v in e.items() if v not in (None, [], {})} | {"type": b.get("type")}
    dump("bases", out)
    return out


# ---------------------------------------------------------------- mods
MOD_FILES = {
    "explicit": "ModExplicit.lua", "itemExclusive": "ModItemExclusive.lua", "jewel": "ModJewel.lua",
    "jewelAbyss": "ModJewelAbyss.lua", "jewelCluster": "ModJewelCluster.lua", "jewelCharm": "ModJewelCharm.lua",
    "flask": "ModFlask.lua", "tincture": "ModTincture.lua", "corrupted": "ModCorrupted.lua",
    "veiled": "ModVeiled.lua", "eldritch": "ModEldritch.lua", "delve": "ModDelve.lua",
    "synthesis": "ModSynthesis.lua", "scourge": "ModScourge.lua", "necropolis": "ModNecropolis.lua",
    "foulborn": "ModFoulborn.lua", "graft": "ModGraft.lua", "map": "ModMap.lua",
}


def mod_entry(k, m):
    if not isinstance(m, dict):
        return None
    text = [m[i] for i in sorted(x for x in m if isinstance(x, int))]
    e = {"id": k, "text": text}
    for f in ("type", "affix", "level", "group"):
        if m.get(f) not in (None, ""):
            e[f] = m[f]
    if m.get("modTags"):
        e["tags"] = m["modTags"]
    wk, wv = m.get("weightKey") or [], m.get("weightVal") or []
    if wk:
        e["weights"] = {str(a): b for a, b in zip(wk, wv)}  # keep 0s: first matching tag wins
    if m.get("types"):
        e["itemTypes"] = sorted(t for t, v in m["types"].items() if v)
    return e


def build_mods(src):
    out = {}
    for cat, fn in MOD_FILES.items():
        p = os.path.join(src, "Data", fn)
        if not os.path.exists(p):
            continue
        try:
            t = lualit.parse_return_table(read(p))
        except Exception as ex:
            print("  skip", fn, ex)
            continue
        if isinstance(t, list):
            t = {f"{cat}{i}": v for i, v in enumerate(t, 1)}
        lst = [e for e in (mod_entry(k, v) for k, v in t.items()) if e]
        out[cat] = lst
    # crafting bench
    t = lualit.parse_return_table(read(os.path.join(src, "Data", "ModMaster.lua")))
    out["crafted"] = [e for e in (mod_entry(f"Crafted{i}", v) for i, v in enumerate(t, 1)) if e]
    print("  mods:", {k: len(v) for k, v in out.items()})
    dump("mods", out)


# ---------------------------------------------------------------- misc
def build_misc(src):
    D = os.path.join(src, "Data")
    misc = {}
    ess = lualit.parse_return_table(read(os.path.join(D, "Essence.lua")))
    misc["essences"] = [{"name": v["name"], "tier": v.get("tier"), "mods": v.get("mods")} for v in ess.values()]
    ench = {}
    for fn in sorted(os.listdir(D)):
        if fn.startswith("Enchantment") and fn.endswith(".lua"):
            try:
                ench[fn[11:-4]] = lualit.parse_return_table(read(os.path.join(D, fn)))
            except Exception as ex:
                print("  skip", fn, ex)
    misc["enchantments"] = ench
    cj = lualit.parse_return_table(read(os.path.join(D, "ClusterJewels.lua")))
    misc["clusterJewels"] = cj
    misc["pantheons"] = lualit.parse_return_table(read(os.path.join(D, "Pantheons.lua")))
    for fn, key in (("Minions.lua", "minions"), ("Spectres.lua", "spectres")):
        mins = lualit.parse_assignments(read(os.path.join(D, fn)), "minions")
        misc[key] = {k: {f: x for f, x in v.items() if isinstance(f, str) and f != "modList"
                         and not isinstance(x, lualit.Raw) and x is not None}
                     for k, v in mins.items() if isinstance(v, dict)}
        for k, v in mins.items():
            if isinstance(v, dict) and isinstance(v.get("modList"), list):
                ml = [luaexpr.clean(_conv(m)) for m in v["modList"]]
                misc[key][k]["modList"] = [m for m in ml if isinstance(m, dict) and m.get("k")]
    for fn, key in (("Bosses.lua", "bosses"), ("BossSkills.lua", "bossSkills")):
        try:
            txt = read(os.path.join(D, fn))
            misc[key] = lualit.parse_return_table(txt) if "return" in txt else lualit.parse_assignments(txt, key)
        except Exception as ex:
            print("  skip", fn, ex)
    try:
        misc["tattoos"] = lualit.parse_return_table(read(os.path.join(D, "TattooPassives.lua")))
    except Exception as ex:
        print("  skip tattoos", ex)
    dump("misc", misc)


# ---------------------------------------------------------------- calc data
def _conv(v):
    """Evaluate Raw PoB expressions (mod()/skill()/flag()/flags) inside a parsed structure."""
    if isinstance(v, lualit.Raw):
        try:
            return luaexpr.evaluate(str(v))
        except Exception:
            return None
    if isinstance(v, dict):
        return {k: _conv(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_conv(x) for x in v]
    return v


def _statmap_entry(e):
    """PoB statMap value -> {"mods": [...], "groups": [{"mods": [...], div, mult, value, base}], div, mult, value, base}"""
    e = _conv(e)
    if isinstance(e, list):
        e = {i + 1: x for i, x in enumerate(e)}
    if not isinstance(e, dict):
        return None
    out = {"mods": [], "groups": []}
    for k, x in e.items():
        if isinstance(k, int):
            if isinstance(x, dict) and x.get("k") in ("mod", "skill", "flag"):
                out["mods"].append(x)
            elif isinstance(x, (dict, list)):
                g = x if isinstance(x, dict) else {i + 1: y for i, y in enumerate(x)}
                grp = {"mods": [y for kk, y in g.items() if isinstance(kk, int) and isinstance(y, dict) and y.get("k")]}
                for f in ("div", "mult", "value", "base"):
                    if isinstance(g.get(f), (int, float)) and not isinstance(g.get(f), bool):
                        grp[f] = g[f]
                out["groups"].append(grp)
        elif k in ("div", "mult", "value", "base") and isinstance(x, (int, float)) and not isinstance(x, bool):
            out[k] = x
        elif k == "skillFlag":
            out["skillFlag"] = x
    return luaexpr.clean(out)


def build_calcdata(src):
    D = os.path.join(src, "Data")
    flags = luaexpr.load_flags(read(os.path.join(D, "Global.lua")))
    # global stat map
    txt = read(os.path.join(D, "SkillStatMap.lua"))
    i = txt.index("return {")
    smap = lualit.parse_return_table(txt[i:])
    statmap = {k: _statmap_entry(v) for k, v in smap.items()}
    # constants
    consts = {"flags": flags, "statMap": {k: v for k, v in statmap.items() if v}}
    mtxt = read(os.path.join(D, "Misc.lua"))
    for name in ("gameConstants", "characterConstants", "monsterConstants", "monsterEvasionTable",
                 "monsterArmourTable", "monsterLifeTable", "monsterLifeTable2", "monsterLifeTable3",
                 "monsterDamageTable", "monsterAccuracyTable", "monsterAllyLifeTable", "monsterAllyDamageTable",
                 "monsterAilmentThresholdTable"):
        m = re.search(rf"^data\.{name}\s*=\s*", mtxt, re.M)
        if m:
            consts[name] = lualit.Parser(mtxt[m.end():]).value()
    # skills with full levels + stat maps
    skills = {}
    for fn in sorted(os.listdir(os.path.join(D, "Skills"))):
        for sid, sk in lualit.parse_assignments(read(os.path.join(D, "Skills", fn)), "skills").items():
            if not isinstance(sk, dict):
                continue
            e = {"name": sk.get("name"), "support": bool(sk.get("support"))}
            for k in ("castTime", "baseEffectiveness", "incrementalEffectiveness", "isTrigger", "cannotBeSupported",
                      "hidden", "fromItem", "fromTree", "minionList", "skillTotemId", "supportGemsOnly"):
                if sk.get(k) is not None and not isinstance(sk.get(k), lualit.Raw):
                    e[k] = sk[k]
            st = sk.get("skillTypes")
            if isinstance(st, dict):
                e["skillTypes"] = sorted(str(k).split(".")[-1] for k, v in st.items() if v)
            for k in ("requireSkillTypes", "addSkillTypes", "excludeSkillTypes"):
                if sk.get(k):
                    e[k] = [str(x).split(".")[-1] for x in sk[k]]
            if isinstance(sk.get("weaponTypes"), dict):
                e["weaponTypes"] = sorted(k for k, v in sk["weaponTypes"].items() if v)
            if isinstance(sk.get("baseFlags"), dict):
                e["baseFlags"] = sorted(k for k, v in sk["baseFlags"].items() if v is True)
            if isinstance(sk.get("addFlags"), dict):
                e["addFlags"] = sorted(k for k, v in sk["addFlags"].items() if v is True)
            if sk.get("parts"):
                e["parts"] = [p.get("name") for p in sk["parts"] if isinstance(p, dict)]
            q = sk.get("qualityStats")
            if isinstance(q, dict):
                q = q.get("Default") or next(iter(q.values()), [])
            if q:
                e["qualityStats"] = q
            if sk.get("constantStats"):
                e["constantStats"] = sk["constantStats"]
            e["stats"] = sk.get("stats") or []
            if any(sk.get(k) for k in ("preDamageFunc", "initialFunc", "postCritFunc", "preSkillTypeFunc")):
                e["customCalc"] = True
            lv = {}
            levels = sk.get("levels") or {}
            if isinstance(levels, list):
                levels = {i: v for i, v in enumerate(levels, 1)}
            for L, row in levels.items():
                if not isinstance(L, int):
                    continue
                if isinstance(row, list):
                    row = {i: v for i, v in enumerate(row, 1)}
                r = {"v": [row.get(i) for i in range(1, len(e["stats"]) + 1)]}
                for k, v in row.items():
                    if isinstance(k, str) and not isinstance(v, lualit.Raw):
                        r[k] = v
                lv[L] = r
            e["levels"] = lv
            if isinstance(sk.get("statMap"), dict):
                e["statMap"] = {k: m for k, m in ((k, _statmap_entry(v)) for k, v in sk["statMap"].items()) if m}
            if sk.get("baseMods"):
                bm = _conv(sk["baseMods"])
                if isinstance(bm, dict):
                    bm = [v for k, v in bm.items() if isinstance(k, int)]
                e["baseMods"] = luaexpr.clean([m for m in bm if isinstance(m, dict) and m.get("k")])
            skills[sid] = e
    dump("skills", skills)
    # mod cache: line -> parsed mods (only lines PoB parsed completely)
    cache = {}
    for line, v in lualit.parse_assignments(read(os.path.join(D, "ModCache.lua")), "c").items():
        if not isinstance(v, list) or not v or not isinstance(v[0], (list, dict)):
            continue
        if len(v) > 1 and v[1]:
            continue  # partially parsed
        mods = v[0] if isinstance(v[0], list) else [v[0][k] for k in sorted(k for k in v[0] if isinstance(k, int))]
        out = []
        for m in mods:
            if not isinstance(m, dict):
                continue
            tags = [m[k] for k in sorted(k for k in m if isinstance(k, int))]
            e = {"name": m.get("name"), "type": m.get("type"), "value": m.get("value"),
                 "flags": m.get("flags", 0), "kw": m.get("keywordFlags", 0)}
            if tags:
                e["tags"] = tags
            out.append(lualit.plain(e))
        if out:
            cache[line] = out
    print("  modcache lines:", len(cache))
    dump("modcache", cache)
    dump("consts", consts)


# ---------------------------------------------------------------- config options
_COLOR = re.compile(r"\^x[0-9A-Fa-f]{6}|\^\d")


def build_config(src):
    """Extract PoB's Config tab: options + the modifiers each one adds (no code is run)."""
    path = os.path.join(src, "Modules", "ConfigOptions.lua")
    txt = read(path)
    starts = [m.start() for m in re.finditer(r"^\t\{ var = \"", txt, re.M)]
    section = None
    sec_pos = [(m.start(), m.group(1)) for m in re.finditer(r'\{ section = "([^"]+)"', txt)]
    opts = []
    for i, st in enumerate(starts):
        body = txt[st: starts[i + 1] if i + 1 < len(starts) else len(txt)]
        for pos, name in sec_pos:
            if pos < st:
                section = name
        head = body.split("apply = function", 1)[0]

        def attr(k, h=head):
            m = re.search(rf'\b{k} = "((?:[^"\\]|\\.)*)"', h)
            return m.group(1) if m else None
        o = {"var": attr("var"), "type": attr("type"), "label": _COLOR.sub("", attr("label") or "").strip(),
             "section": section}
        tip = attr("tooltip")
        if tip:
            o["tooltip"] = _COLOR.sub("", tip.replace("\\n", " ").replace("\\t", " "))[:300]
        m = re.search(r"\blist = (\{\{.*?\}\})", head, re.S)
        if m:
            o["list"] = [{"val": a, "label": _COLOR.sub("", b)} for a, b in
                         re.findall(r'val\s*=\s*"?([^",}]*)"?\s*,\s*label\s*=\s*"([^"]*)"', m.group(1))]
        for k in ("defaultIndex", "defaultState", "defaultPlaceholderState"):
            mm = re.search(rf"\b{k} = ([\w.]+)", head)
            if mm:
                v = mm.group(1)
                o[k] = True if v == "true" else False if v == "false" else (float(v) if re.match(r"^-?\d+(\.\d+)?$", v) else v)
        for k in ("ifCond", "ifEnemyCond", "ifFlag", "ifSkill", "ifOption", "ifMult", "ifStat", "ifEnemyStat"):
            v = attr(k)
            if v:
                o[k] = v
        mods = []
        if "apply = function" in body:
            ab = body.split("apply = function", 1)[1]
            cond = None
            for mm in re.finditer(r'(?:(else)?if val == "([^"]*)" then)|(?:\belse\b)|(?:(modList|enemyModList):NewMod\()', ab):
                if mm.group(2) is not None:
                    cond = mm.group(2)
                    continue
                if mm.group(3) is None:
                    cond = "__else__"
                    continue
                p = lualit.Parser(ab[mm.end() - 1:])
                try:
                    ev = luaexpr.Eval(ab[mm.end() - 1:], ["val"])
                    ev.p.expect("(")
                    args = []
                    while not ev.p.is_(")"):
                        args.append(ev.value())
                        if ev.p.is_(","):
                            ev.p.next()
                except Exception:
                    continue
                if len(args) < 3 or not isinstance(args[0], str):
                    continue
                rest = args[4:]
                flags = rest[0] if rest and isinstance(rest[0], int) else 0
                kw = rest[1] if len(rest) > 1 and isinstance(rest[1], int) else 0
                tags = [t for t in rest if isinstance(t, dict) and "type" in t]
                mod = {"target": "enemy" if mm.group(3) == "enemyModList" else "player", "name": args[0],
                       "type": args[1], "value": args[2], "flags": flags, "kw": kw, "tags": tags}
                if cond:
                    mod["when"] = cond
                mods.append(luaexpr.clean(mod))
            del p
        o["mods"] = mods
        opts.append(o)
    print("  config options:", len(opts), "with mods:", sum(1 for o in opts if o["mods"]))
    dump("config", opts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="path to PathOfBuilding/src")
    ap.add_argument("--tree", default="3_30")
    a = ap.parse_args()
    luaexpr.load_flags(read(os.path.join(a.src, "Data", "Global.lua")))
    print("tree", a.tree)
    build_tree(a.src, a.tree)
    print("gems")
    build_gems(a.src)
    print("uniques")
    build_uniques(a.src)
    print("bases")
    build_bases(a.src)
    print("mods")
    build_mods(a.src)
    print("misc")
    build_misc(a.src)
    print("calc data")
    build_calcdata(a.src)
    print("config")
    build_config(a.src)
    meta = {"treeVersion": a.tree, "source": "PathOfBuildingCommunity/PathOfBuilding (MIT)",
            "gameData": "(c) Grinding Gear Games"}
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)


if __name__ == "__main__":
    main()

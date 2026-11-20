"""Calculation engine modelled on Path of Building (CalcSetup/CalcPerform/CalcDefence/CalcOffence).

Everything is driven by structured modifiers: tree/item lines are parsed with
PoB's own ModCache (modparse), gem stats are mapped with PoB's statMap data.
Covered: attributes, life/mana/ES/ward, armour/evasion, resists, block,
suppression, regen, reservation, movement, max hit; main-skill hit DPS
(spells & attacks incl. dual wield), conversion & gain-as-extra, crit, accuracy,
penetration & enemy resist, skill DoT, ignite/bleed/poison, auras/heralds/buffs
and curses from other socket groups, charges and all PoB Config options.
Not covered (yet): minions, triggers, totem/trap/mine counts beyond 1, cluster
& timeless jewel trees, many special-case uniques. Results are labelled.
"""
from __future__ import annotations

import copy
import functools
import math
from collections import defaultdict

from . import data, items as itemsmod, modparse

DMG_TYPES = ["Physical", "Lightning", "Cold", "Fire", "Chaos"]
ELEMENTAL = {"Lightning", "Cold", "Fire"}


@functools.lru_cache(maxsize=None)
def consts():
    return data.load("consts")


@functools.lru_cache(maxsize=None)
def skills_data():
    return data.load("skills")


@functools.lru_cache(maxsize=None)
def config_options():
    opts = list(data.load("config"))
    opts.append({"var": "customMods", "type": "text", "section": "General", "mods": [],
                 "label": "Custom modifiers: mod lines (one per line) applied to the player, like PoB's Config > Custom Modifiers"})
    return opts


@functools.lru_cache(maxsize=None)
def _skill_type_names():
    return {v: k for k, v in consts()["flags"].get("SkillType", {}).items()}


def skill_type_name(x):
    """PoB's ModCache stores SkillType as numbers; skill data uses names."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return _skill_type_names().get(int(x), x)
    return x


def MF(name):
    return consts()["flags"]["ModFlag"].get(name, 0)


def KF(name):
    return consts()["flags"]["KeywordFlag"].get(name, 0)


def CC(name, default=0):
    return consts()["characterConstants"].get(name, default)


def rnd(x, n=0):
    m = 10 ** n
    return math.floor(x * m + 0.5) / m


def norm_mod(m, source=None):
    """Normalise a nested PoB mod table (keywordFlags / numbered tag keys) to our mod dict."""
    out = {"name": m.get("name"), "type": m.get("type"), "value": m.get("value"),
           "flags": m.get("flags", 0) or 0, "kw": m.get("kw", m.get("keywordFlags", 0)) or 0}
    tags = list(m.get("tags") or [])
    for k, v in m.items():
        if (isinstance(k, int) or (isinstance(k, str) and k.isdigit())) and isinstance(v, dict):
            tags.append(v)
    out["tags"] = tags
    if source:
        out["source"] = source
    return out


# ======================================================================== ModDB
class ModDB:
    def __init__(self, actor, parent=None):
        self.actor = actor
        self.parent = parent
        self.mods = defaultdict(list)
        self.conditions = {}
        self.multipliers = {}

    def add(self, mod, source=None):
        m = dict(mod)
        if source and "source" not in m:
            m["source"] = source
        self.mods[m["name"]].append(m)

    def add_list(self, mods, source=None):
        for m in mods:
            self.add(m, source)

    def new(self, name, typ, value, source="Base", flags=0, kw=0, *tags):
        self.add({"name": name, "type": typ, "value": value, "flags": flags, "kw": kw, "tags": list(tags),
                  "source": source})

    def _iter(self, names):
        db = self
        while db is not None:
            for n in names:
                for m in db.mods.get(n, ()):
                    yield m
            db = db.parent

    # -- matching ------------------------------------------------------
    @staticmethod
    def _flags_match(m, cfg):
        f = m.get("flags", 0) or 0
        if f and (cfg.get("flags", 0) & f) != f:
            return False
        kw = m.get("kw", 0) or 0
        if kw:
            match_all = KF("MatchAll")
            ckw = cfg.get("kw", 0) & ~match_all
            if kw & match_all:
                kw &= ~match_all
                return (ckw & kw) == kw
            return (ckw & kw) != 0
        return True

    def get_condition(self, var, cfg):
        if self.conditions.get(var):
            return True
        if self.parent is not None and self.parent.conditions.get(var):
            return True
        if cfg and cfg.get("skillCond", {}).get(var):
            return True
        return self.flag(cfg, "Condition:" + var)

    def get_multiplier(self, var, cfg):
        ov = self.override(cfg, "Multiplier:" + var)
        if ov is not None:
            return ov
        v = self.multipliers.get(var, 0)
        if self.parent is not None:
            v += self.parent.multipliers.get(var, 0)
        return v + self.sum("BASE", cfg, "Multiplier:" + var)

    def get_stat(self, stat, cfg):
        return (self.actor.output.get(stat) or 0) if self.actor else 0

    def _target(self, actor_name):
        if not actor_name or not self.actor:
            return self
        a = self.actor.other if actor_name in ("enemy", "player") and self.actor.other else None
        if actor_name == "parent":
            return None
        return a.moddb if a else None

    def eval(self, m, cfg):
        """Return the mod's effective value, or None if its tags exclude it."""
        value = m.get("value")
        cfg = cfg or {}
        for t in m.get("tags", ()):
            tt = t.get("type")
            if tt in ("Multiplier", "PerStat", "PercentStat"):
                tgt = self._target(t.get("actor")) if tt == "Multiplier" and t.get("actor") else self
                if tgt is None:
                    return None
                if tt == "Multiplier":
                    names = t.get("varList") or [t.get("var")]
                    base = sum(tgt.get_multiplier(v, cfg) for v in names if v)
                else:
                    names = t.get("statList") or [t.get("stat")]
                    base = sum(tgt.get_stat(s, cfg) for s in names if s)
                if tt == "PercentStat":
                    mult = base * ((t.get("percent") or 100) / 100)
                    if t.get("floor"):
                        mult = math.floor(mult)
                else:
                    div = t.get("div") or 1
                    mult = base / div if t.get("noFloor") else math.floor(base / div + 0.0001)
                lim = t.get("limit")
                if lim is None and t.get("limitVar"):
                    lim = tgt.get_multiplier(t["limitVar"], cfg) or None
                if lim is None and t.get("limitStat"):
                    lim = tgt.get_stat(t["limitStat"], cfg) or tgt.sum("BASE", cfg, t["limitStat"]) or None
                limit_total = None
                if lim is not None:
                    if t.get("limitTotal"):
                        limit_total = lim
                    else:
                        mult = min(mult, lim)
                if t.get("invert") and mult:
                    mult = 1 / mult

                def apply(v):
                    nv = v * mult + (t.get("base") or 0)
                    if tt == "PercentStat":
                        nv = math.ceil(nv)
                    return min(nv, limit_total) if limit_total is not None else nv
                if isinstance(value, dict):
                    value = copy.deepcopy(value)
                    if isinstance(value.get("mod"), dict):
                        value["mod"]["value"] = apply(value["mod"]["value"])
                    elif "value" in value and isinstance(value["value"], (int, float)):
                        value["value"] = apply(value["value"])
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    value = apply(value)
            elif tt == "MultiplierThreshold":
                tgt = self._target(t.get("actor")) if t.get("actor") else self
                if tgt is None:
                    return None
                names = t.get("varList") or [t.get("var")]
                mult = sum(tgt.get_multiplier(v, cfg) for v in names if v)
                thr = t.get("threshold")
                if thr is None:
                    thr = tgt.get_multiplier(t.get("thresholdVar"), cfg) if t.get("thresholdVar") else 0
                if (t.get("upper") and mult > thr) or (t.get("equals") and mult != thr) or (not t.get("upper") and mult < thr):
                    return None
            elif tt == "StatThreshold":
                names = t.get("statList") or [t.get("stat")]
                st = sum(self.get_stat(s, cfg) for s in names if s)
                thr = t.get("threshold")
                if thr is None:
                    thr = self.get_stat(t.get("thresholdStat"), cfg)
                if t.get("thresholdPercent"):
                    thr *= t["thresholdPercent"] / 100
                if (t.get("upper") and st > thr) or (not t.get("upper") and st < thr):
                    return None
            elif tt == "Limit":
                lim = t.get("limit")
                if lim is None:
                    lim = self.get_multiplier(t.get("limitVar"), cfg)
                value = min(value, lim)
            elif tt == "Condition":
                names = t.get("varList") or [t.get("var")]
                match = any(self.get_condition(v, cfg) for v in names if v)
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "ActorCondition":
                tgt = self._target(t.get("actor"))
                if tgt is not None and (t.get("var") or t.get("varList")):
                    names = t.get("varList") or [t.get("var")]
                    match = any(tgt.get_condition(v, cfg) for v in names)
                else:
                    match = bool(t.get("actor") and cfg.get("actor") == t.get("actor"))
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "SocketedIn":
                if not cfg.get("slotName") and not t.get("keyword"):
                    return None
                ok = True
                if t.get("slotName") and t["slotName"] != cfg.get("slotName"):
                    ok = False
                if ok and t.get("keyword"):
                    ok = gem_is_type(cfg.get("gem"), t["keyword"])
                if t.get("neg"):
                    ok = not ok
                if not ok:
                    return None
            elif tt == "SkillName":
                nm = (cfg.get("skillName") or "").lower()
                lst = t.get("skillNameList") or [t.get("skillName")]
                match = any((x or "").lower() == nm for x in lst)
                if not match and t.get("includeTransfigured"):
                    match = any(nm.startswith((x or "").lower()) for x in lst if x)
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "SkillId":
                if cfg.get("skillId") != t.get("skillId"):
                    return None
            elif tt == "SkillPart":
                lst = t.get("skillPartList") or [t.get("skillPart")]
                match = cfg.get("skillPart", 1) in lst
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "SkillType":
                st = cfg.get("skillTypes") or set()
                lst = t.get("skillTypeList") or [t.get("skillType")]
                match = any(skill_type_name(x) in st for x in lst)
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "BaseFlag":
                match = t.get("baseFlag") in (cfg.get("baseFlags") or set())
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "SlotName":
                lst = t.get("slotNameList") or [t.get("slotName")]
                match = cfg.get("slotName") in lst
                if t.get("neg"):
                    match = not match
                if not match:
                    return None
            elif tt == "ModFlagOr":
                if not (cfg.get("flags", 0) & (t.get("modFlags") or 0)):
                    return None
            elif tt == "KeywordFlagAnd":
                kf = t.get("keywordFlags") or 0
                if (cfg.get("kw", 0) & kf) != kf:
                    return None
            elif tt == "ItemCondition":
                if not self._item_condition(t, cfg):
                    return None
            elif tt in ("Global", "GlobalEffect", "IgnoreCond", "InSlot", "SlotNumber"):
                pass
            elif tt in ("DistanceRamp", "MeleeProximity"):
                dist = cfg.get("skillDist")
                if dist is None:
                    return None
                ramp = t.get("ramp") or []
                if tt == "DistanceRamp" and ramp:
                    if dist <= ramp[0][0]:
                        value *= ramp[0][1]
                    elif dist >= ramp[-1][0]:
                        value *= ramp[-1][1]
                    else:
                        for i in range(len(ramp) - 1):
                            a, b = ramp[i], ramp[i + 1]
                            if dist <= b[0]:
                                value *= a[1] + (b[1] - a[1]) * (dist - a[0]) / (b[0] - a[0])
                                break
                elif ramp:
                    value = value * ramp[0] if dist <= 15 else (value * (ramp[0] - ramp[0] / 25 * (dist - 15)) if dist < 40 else 0)
            else:
                # ItemCondition, MonsterTag, DisablesItem, ... not modelled
                return None
        return value

    def _item_condition(self, t, cfg):
        """PoB ItemCondition tag (e.g. 'if there are no Life Modifiers on Equipped Body Armour')."""
        item_list = getattr(self.actor, "items", None) or {}
        slot = " ".join(w.capitalize() for w in (t.get("itemSlot") or "").strip().split())
        if t.get("allSlots"):
            items = dict(item_list)
        elif t.get("bothSlots"):
            a, b = item_list.get(slot + " 1"), item_list.get(slot + " 2")
            items = {slot + " 1": a, slot + " 2": b} if a and b else {}
        else:
            items = {slot: item_list[slot]} if slot in item_list else {}
        matches = []
        for sl, it in items.items():
            lines = [x.lower() for x in itemsmod.active_mod_lines(it)]
            if t.get("searchCond"):
                if not (t.get("excludeSelf") and sl == slot):
                    matches.append(any(t["searchCond"].lower() in x for x in lines))
            if t.get("rarityCond"):
                matches.append(it["rarity"] == t["rarityCond"])
            if t.get("corruptedCond") is not None:
                matches.append(("Corrupted" in it.get("flags", [])) == t["corruptedCond"])
            if t.get("nameCond"):
                matches.append(f"{it['name']}, {it['base']}".lower() == t["nameCond"].lower())
        neg = bool(t.get("neg"))
        if any(m == neg for m in matches):
            return False
        if not items and not neg:
            return False
        return True

    # -- aggregation ----------------------------------------------------
    def _matching(self, typ, cfg, names):
        cfg = cfg or {}
        for m in self._iter(names):
            if m.get("type") != typ or not self._flags_match(m, cfg):
                continue
            v = self.eval(m, cfg)
            if v is None:
                continue
            yield m, v

    def sum(self, typ, cfg, *names):
        t = 0.0
        for m, v in self._matching(typ, cfg, names):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                t += v
        return t

    def more(self, cfg, *names):
        r = 1.0
        for m, v in self._matching("MORE", cfg, names):
            if isinstance(v, (int, float)):
                r *= 1 + v / 100
        return max(r, 0)

    def flag(self, cfg, *names):
        for m, v in self._matching("FLAG", cfg, names):
            if v:
                return True
        return False

    def override(self, cfg, *names):
        for m, v in self._matching("OVERRIDE", cfg, names):
            return v
        return None

    def list(self, cfg, *names):
        return [v for m, v in self._matching("LIST", cfg, names)]

    def tabulate(self, typ, cfg, *names):
        return [{"value": v, "source": m.get("source", "?"), "name": m["name"]}
                for m, v in self._matching(typ, cfg, names)]


class Actor:
    def __init__(self, name):
        self.name = name
        self.output = {}
        self.other = None
        self.moddb = ModDB(self)


# ======================================================================== gems
def gem_is_type(gem, kw):
    if not gem:
        return False
    kw = (kw or "").lower()
    tags = set(gem.get("tagList", []))
    if kw == "all":
        return True
    if kw == "elemental":
        return bool(tags & {"fire", "cold", "lightning"})
    if kw == "aoe":
        return "area" in tags
    if kw in ("active skill", "grants_active_skill", "skill"):
        return "grants_active_skill" in tags and not gem.get("support")
    if kw == "trap or mine":
        return bool(tags & {"trap", "mine"})
    if kw == "non-vaal":
        return "vaal" not in tags
    if kw == gem["name"].lower():
        return True
    return kw in tags


def gem_stats(sk, level, quality):
    """PoB calcLib.buildSkillInstanceStats."""
    stats = {}
    if quality and sk.get("qualityStats"):
        for q in sk["qualityStats"]:
            if isinstance(q, list) and len(q) >= 2:
                stats[q[0]] = stats.get(q[0], 0) + math.modf(q[1] * quality)[1]
    levels = sk.get("levels") or {}
    if not levels:
        return stats, {}
    keys = sorted(int(k) for k in levels)
    lv = min(max(level, keys[0]), keys[-1])
    if str(lv) not in levels:
        lv = max(k for k in keys if k <= lv)
    row = levels[str(lv)]
    actor_level = row.get("levelRequirement") or 1
    interp = row.get("statInterpolation") or []
    eff = None
    for i, st in enumerate(sk.get("stats", [])):
        v = row["v"][i] if i < len(row["v"]) and row["v"][i] is not None else 1
        it = interp[i] if i < len(interp) else 1
        if it == 3:
            if eff is None:
                g = consts()["gameConstants"]
                eff = ((g["SkillDamageBaseEffectiveness"] + g["SkillDamageIncrementalEffectiveness"] * (actor_level - 1))
                       * (sk.get("baseEffectiveness") or 1) * (1 + (sk.get("incrementalEffectiveness") or 0)) ** (actor_level - 1))
            v = rnd(eff * v)
        elif it == 2 and len(keys) > 1:
            idx = keys.index(lv)
            n = keys[min(idx + 1, len(keys) - 1)]
            p = keys[min(idx + 1, len(keys) - 1) - 1]
            nr, pr = levels[str(n)], levels[str(p)]
            if nr.get("levelRequirement") != pr.get("levelRequirement") and nr["v"][i] is not None and pr["v"][i] is not None:
                v = rnd(pr["v"][i] + (nr["v"][i] - pr["v"][i]) * (actor_level - pr["levelRequirement"]) /
                        (nr["levelRequirement"] - pr["levelRequirement"]))
        stats[st] = stats.get(st, 0) + v
    for c in sk.get("constantStats") or []:
        if isinstance(c, list) and c:
            stats[c[0]] = stats.get(c[0], 0) + (c[1] if len(c) > 1 and c[1] is not None else 0)
    return stats, row


def _to_mod(entry, value, source):
    """statMap entry mod -> concrete mod dict (or skill data / flag)."""
    k = entry.get("k")
    tags = [dict(t) for t in entry.get("tags") or [] if isinstance(t, dict)]
    if k == "skill":
        v = entry.get("value")
        return {"name": "SkillData", "type": "LIST", "value": {"key": entry["name"], "value": value if v is None else v},
                "flags": 0, "kw": 0, "tags": tags, "source": source}
    if k == "flag":
        return {"name": entry["name"], "type": "FLAG", "value": True, "flags": 0, "kw": 0, "tags": tags, "source": source}
    v = entry.get("value")
    if isinstance(v, dict):
        v = copy.deepcopy(v)
        if isinstance(v.get("mod"), dict):
            if v["mod"].get("value") is None:
                v["mod"]["value"] = value
        elif v.get("value") is None:
            v["value"] = value
    elif v is None:
        v = value
    return {"name": entry["name"], "type": entry.get("type"), "value": v, "flags": entry.get("flags") or 0,
            "kw": entry.get("kw") or 0, "tags": tags, "source": source}


def skill_instance_mods(sk, level, quality, source):
    """Mods + skill data granted by one gem at a level (mergeSkillInstanceMods)."""
    stats, row = gem_stats(sk, level, quality)
    smap_own = sk.get("statMap") or {}
    smap_glob = consts()["statMap"]
    mods = []
    unmapped = []
    for stat, val in stats.items():
        entry = smap_own.get(stat) or smap_glob.get(stat)
        if not entry:
            unmapped.append(stat)
            continue

        def scaled(e, v=val):
            if e.get("value") is not None:
                return e["value"]
            return v * (e.get("mult") or 1) / (e.get("div") or 1) + (e.get("base") or 0)
        for m in entry.get("mods", []):
            mods.append(_to_mod(m, scaled(entry), source))
        for g in entry.get("groups", []):
            for m in g.get("mods", []):
                mods.append(_to_mod(m, scaled(g), source))
    for bm in sk.get("baseMods") or []:
        mods.append(_to_mod(bm, bm.get("value"), source))
    return mods, row, stats, unmapped


# ======================================================================== items
LOCAL_DEF = ("Armour", "Evasion", "EnergyShield", "Ward", "ArmourAndEvasion", "ArmourAndEnergyShield",
             "EvasionAndEnergyShield", "Defences")


def _is_local(m, flags=0):
    return m.get("flags", 0) == flags and not m.get("kw") and not m.get("tags")


def parse_item_mods(text, slot):
    """Returns (item, base, global_mods, weapon_data|None, armour_data|None, unparsed_lines)."""
    it = itemsmod.parse_item(text)
    fb = data.find_base(it["base"])
    base = fb[1] if fb else {}
    mods, unparsed = [], []
    for line in itemsmod.active_mod_lines(it):
        for sub in line.split("\n"):
            r = modparse.parse_line(modparse.roll(sub))
            if r is None:
                unparsed.append(sub)
                continue
            for m in r:
                for t in m.get("tags", []) or []:
                    for k, v in list(t.items()):
                        if isinstance(v, str):
                            t[k] = v.replace("{SlotName}", slot).replace(
                                "{Hand}", "OffHand" if slot.endswith("2") else "MainHand")
                mods.append(m)
    quality = 0
    q = it["props"].get("Quality")
    if q:
        try:
            quality = float("".join(c for c in q if c.isdigit() or c == ".") or 0)
        except ValueError:
            quality = 0
    if any(m["name"] == "AlternateQualityArmour" for m in mods) and base.get("armour"):
        quality_def = 0
    else:
        quality_def = quality
    mods = [m for m in mods if m["name"] != "AlternateQualityArmour"]
    if quality:
        mods.append({"name": "Multiplier:QualityOn" + slot, "type": "BASE", "value": quality, "flags": 0, "kw": 0,
                     "source": "Quality"})
    rest = []
    weapon = armour = None
    if base.get("weapon"):
        w = base["weapon"]
        loc = [m for m in mods if _is_local(m) or (_is_local(m, MF("Attack")) and m["name"] == "Speed")]
        def L(name, typ, flags=0):
            return sum(m["value"] for m in loc if m["name"] == name and m["type"] == typ
                       and m.get("flags", 0) == flags and isinstance(m["value"], (int, float)))
        weapon = {"type": base["type"], "subType": base.get("subType")}
        weapon["AttackRate"] = rnd(w.get("AttackRateBase", 1) * (1 + L("Speed", "INC", MF("Attack")) / 100), 2)
        loc_ele = L("LocalElementalDamage", "INC")
        for dt in DMG_TYPES:
            mn = (w.get(dt + "Min") or 0) + L(dt + "Min", "BASE")
            mx = (w.get(dt + "Max") or 0) + L(dt + "Max", "BASE")
            if dt == "Physical":
                inc = L("PhysicalDamage", "INC")
                mn, mx = rnd(mn * (1 + inc / 100) * (1 + quality / 100)), rnd(mx * (1 + inc / 100) * (1 + quality / 100))
            elif dt != "Chaos":
                inc = L("Local" + dt + "Damage", "INC") + loc_ele
                mn, mx = rnd(mn * (1 + inc / 100)), rnd(mx * (1 + inc / 100))
            if mn > 0 and mx > 0:
                weapon[dt + "Min"], weapon[dt + "Max"] = mn, mx
        weapon["CritChance"] = rnd((w.get("CritChanceBase", 5) + L("CritChance", "BASE")) * (1 + L("CritChance", "INC") / 100), 2)
        weapon["range"] = w.get("Range")
        local_names = {"PhysicalDamage", "CritChance", "LocalElementalDamage"} | {d + "Min" for d in DMG_TYPES} | {d + "Max" for d in DMG_TYPES}
        for m in mods:
            if (m in loc and (m["name"] in local_names or m["name"] == "Speed")) and m["type"] in ("BASE", "INC"):
                continue
            if m["name"].startswith("Local") and m["name"].endswith("Damage"):
                continue
            # accuracy on weapons is local to attacks with that weapon
            if m["name"] == "Accuracy" and _is_local(m):
                m = dict(m, tags=[{"type": "Condition", "var": "OffHandAttack" if slot.endswith("2") else "MainHandAttack"}])
            rest.append(m)
    elif base.get("armour"):
        a = base["armour"]
        loc = [m for m in mods if _is_local(m) and m["name"] in LOCAL_DEF and m["type"] in ("BASE", "INC")]
        def L(name, typ):
            return sum(m["value"] for m in loc if m["name"] == name and m["type"] == typ)
        dinc = L("Defences", "INC")
        armour = {}
        for key, inc_names, base_names in (
                ("Armour", ("Armour", "ArmourAndEvasion", "ArmourAndEnergyShield"), ("Armour", "ArmourAndEvasion", "ArmourAndEnergyShield")),
                ("Evasion", ("Evasion", "ArmourAndEvasion", "EvasionAndEnergyShield"), ("Evasion", "ArmourAndEvasion", "EvasionAndEnergyShield")),
                ("EnergyShield", ("EnergyShield", "ArmourAndEnergyShield", "EvasionAndEnergyShield"), ("EnergyShield", "ArmourAndEnergyShield", "EvasionAndEnergyShield")),
                ("Ward", ("Ward",), ("Ward",))):
            bmin = a.get(key + "BaseMin", 0) or 0
            bmax = a.get(key + "BaseMax", bmin) or 0
            pct = it["props"].get(key + "BasePercentile")
            try:
                pct = float(pct) if pct is not None else 1.0
            except ValueError:
                pct = 1.0
            bmax = round(bmin + (bmax - bmin) * pct)
            b = bmax + sum(L(n, "BASE") for n in base_names)
            inc = sum(L(n, "INC") for n in inc_names) + dinc
            armour[key] = rnd(b * (1 + inc / 100) * (1 + quality_def / 100)) if b else 0
        if a.get("BlockChance"):
            armour["BlockChance"] = a["BlockChance"] + sum(m["value"] for m in mods if m["name"] == "BlockChance"
                                                            and m["type"] == "BASE" and _is_local(m))
        armour["MovementPenalty"] = a.get("MovementPenalty")
        for m in mods:
            if m in loc:
                continue
            if armour.get("BlockChance") and m["name"] == "BlockChance" and m["type"] == "BASE" and _is_local(m):
                continue
            rest.append(m)
    else:
        rest = mods
    return it, base, rest, weapon, armour, unparsed


# ======================================================================== main calc
class Env:
    pass


def eval_formula(f, env):
    """Evaluate config formulas extracted from PoB (e.g. min(val - 1, 9))."""
    if isinstance(f, dict):
        if "var" in f and len(f) == 1:
            v = env.get(f["var"], 0)
            try:
                return float(v) if not isinstance(v, (int, float)) else v
            except (TypeError, ValueError):
                return v
        if "op" in f:
            a, b = eval_formula(f["a"], env), eval_formula(f["b"], env)
            return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0}[f["op"]]
        if "fn" in f:
            xs = [eval_formula(x, env) for x in f["args"]]
            fn = f["fn"]
            if fn == "min":
                return min(xs)
            if fn == "max":
                return max(xs)
            if fn == "round":
                return rnd(xs[0], int(xs[1]) if len(xs) > 1 else 0)
            if fn == "floor":
                return math.floor(xs[0])
            if fn == "ceil":
                return math.ceil(xs[0])
            return xs[0] if xs else 0
        return {k: eval_formula(v, env) for k, v in f.items()}
    if isinstance(f, list):
        return [eval_formula(x, env) for x in f]
    return f


def _cfg_inputs(build):
    return dict(build.config or {})


def apply_config(build, player, enemy, cfg_in):
    """Apply PoB Config options (same variable names as PoB)."""
    opts = {o["var"]: o for o in config_options()}
    # PoB uses an option's placeholder value when it is left empty (e.g. 5 Spectral Tigers)
    cfg_in = dict(cfg_in)
    for o in config_options():
        if o["var"] not in cfg_in and isinstance(o.get("defaultPlaceholderState"), (int, float)) \
                and not isinstance(o.get("defaultPlaceholderState"), bool) and o.get("type") in ("count", "integer", "countAllowZero"):
            cfg_in[o["var"]] = o["defaultPlaceholderState"]
    for k, v in (getattr(build, "config_placeholders", None) or {}).items():
        if not k.startswith("enemy"):  # enemy defaults come from boss_defaults()
            cfg_in.setdefault(k, v)
    custom = cfg_in.get("customMods")
    if custom:
        for line in str(custom).replace("\r", "").split("\n"):
            line = line.strip()
            if not line:
                continue
            r = modparse.parse_line(modparse.roll(line))
            if r is None:
                build._custom_unparsed = getattr(build, "_custom_unparsed", []) + [line]
            else:
                player.moddb.add_list(r, "Custom")
    for var, val in cfg_in.items():
        o = opts.get(var)
        if not o or val in (None, False, ""):
            continue
        for m in o.get("mods", []):
            when = m.get("when")
            if when and when != "__else__" and str(val) != when:
                continue
            if when == "__else__":
                continue
            v = m.get("value")
            v = eval_formula(v, {"val": val if not isinstance(val, bool) else 1})
            tags = [t for t in m.get("tags", []) if t.get("type") != "IgnoreCond"]
            db = enemy.moddb if m.get("target") == "enemy" else player.moddb
            db.add({"name": m["name"], "type": m["type"], "value": v, "flags": m.get("flags", 0),
                    "kw": m.get("kw", 0), "tags": tags, "source": "Config"})


def boss_defaults(cfg_in, char_level):
    boss = cfg_in.get("enemyIsBoss", "Pinnacle")
    lvl = cfg_in.get("enemyLevel")
    if boss == "Pinnacle":
        d = {"res": 50, "chaos": 30, "level": max(int(lvl or 0), 84), "pen": 3}
    elif boss == "Uber":
        d = {"res": 50, "chaos": 30, "level": max(int(lvl or 0), 85), "pen": 8}
    elif boss == "Boss":
        d = {"res": 40, "chaos": 25, "level": int(lvl or min(83, char_level)), "pen": 0}
    else:
        d = {"res": 0, "chaos": 0, "level": int(lvl or min(83, char_level)), "pen": 0}
    d["boss"] = boss
    return d


class Calculator:
    def __init__(self, build):
        self.build = build
        self.notes = []
        self.unparsed = []

    # -------------------------------------------------------------- setup
    def setup(self):
        b = self.build
        self.cfg_in = _cfg_inputs(b)
        player, enemy = Actor("player"), Actor("enemy")
        player.other, enemy.other = enemy, player
        self.player, self.enemy = player, enemy
        db = player.moddb
        cls = data.classes()[b.class_id]
        level = max(1, min(100, b.level))
        db.multipliers["Level"] = level
        for st, k in (("Str", "str"), ("Dex", "dex"), ("Int", "int")):
            db.new(st, "BASE", cls[k])
        db.new("Life", "BASE", CC("life_per_level"), "Base", 0, 0, {"type": "Multiplier", "var": "Level", "base": 38})
        db.new("Mana", "BASE", CC("mana_per_level"), "Base", 0, 0, {"type": "Multiplier", "var": "Level", "base": 34})
        db.new("Evasion", "BASE", CC("base_evasion_rating"))
        db.new("Accuracy", "BASE", CC("accuracy_rating_per_level"), "Base", 0, 0,
               {"type": "Multiplier", "var": "Level", "base": -CC("accuracy_rating_per_level")})
        db.new("CritMultiplier", "BASE", CC("base_critical_strike_multiplier") - 100)
        db.new("DotMultiplier", "BASE", CC("critical_ailment_dot_multiplier_+"), "Base", 0, 0, {"type": "Condition", "var": "CriticalStrike"})
        pen = float(self.cfg_in.get("resistancePenalty", -60) or 0)
        for r in ("Fire", "Cold", "Lightning", "Chaos"):
            db.new(r + "Resist", "BASE", pen)
            db.new(r + "ResistMax", "BASE", CC("base_maximum_all_resistances_%"))
        db.new("BlockChanceMax", "BASE", CC("maximum_block_%"))
        db.new("SpellBlockChanceMax", "BASE", CC("base_maximum_spell_block_%"))
        for ch, k in (("PowerChargesMax", "max_power_charges"), ("FrenzyChargesMax", "max_frenzy_charges"),
                      ("EnduranceChargesMax", "max_endurance_charges")):
            db.new(ch, "BASE", CC(k))
        db.new("CritChance", "INC", CC("critical_strike_chance_+%_per_power_charge"), "Base", 0, 0, {"type": "Multiplier", "var": "PowerCharge"})
        db.new("Speed", "INC", CC("base_attack_speed_+%_per_frenzy_charge"), "Base", MF("Attack"), 0, {"type": "Multiplier", "var": "FrenzyCharge"})
        db.new("Speed", "INC", CC("base_cast_speed_+%_per_frenzy_charge"), "Base", MF("Cast"), 0, {"type": "Multiplier", "var": "FrenzyCharge"})
        db.new("Damage", "MORE", CC("object_inherent_damage_+%_final_per_frenzy_charge"), "Base", 0, 0, {"type": "Multiplier", "var": "FrenzyCharge"})
        db.new("PhysicalDamageReduction", "BASE", CC("physical_damage_reduction_%_per_endurance_charge"), "Base", 0, 0, {"type": "Multiplier", "var": "EnduranceCharge"})
        db.new("ElementalDamageReduction", "BASE", CC("elemental_damage_reduction_%_per_endurance_charge"), "Base", 0, 0, {"type": "Multiplier", "var": "EnduranceCharge"})
        db.new("Speed", "MORE", CC("dual_wield_inherent_attack_speed_+%_final"), "Base", MF("Attack"), 0, {"type": "Condition", "var": "DualWielding"})
        db.new("BlockChance", "BASE", CC("inherent_block_while_dual_wielding_%"), "Base", 0, 0, {"type": "Condition", "var": "DualWielding"})
        db.new("MaximumRage", "BASE", CC("maximum_rage"))
        db.new("Damage", "MORE", 200, "Base", 0, KF("Bleed"), {"type": "ActorCondition", "actor": "enemy", "var": "Moving"})
        db.new("EnemyCurseLimit", "BASE", 1)
        db.new("ActiveTotemLimit", "BASE", 1)
        db.new("Multiplier:IntensityLimit", "BASE", 3)
        db.new("ProjectileCount", "BASE", 1)
        db.new("MovementSpeed", "INC", 0)
        bandit = b.bandit or "None"
        if bandit == "Alira":
            db.new("ElementalResist", "BASE", 15, "Bandit")
        elif bandit == "Kraityn":
            db.new("MovementSpeed", "INC", 8, "Bandit")
        elif bandit == "Oak":
            db.new("Life", "BASE", 40, "Bandit")
        db.conditions["Combat"] = True
        db.conditions["Effective"] = True
        enemy.moddb.conditions["Effective"] = True
        enemy.moddb.conditions["Combat"] = True
        # pantheon
        for god in (b.pantheon_major, b.pantheon_minor):
            if god and god != "None":
                p = data.misc().get("pantheons", {}).get(god)
                if p:
                    for soul in (p.get("souls") or [])[:1] if self.cfg_in.get("pantheonUpgrades") is None else p.get("souls") or []:
                        for sm in (soul.get("mods") or []):
                            r = modparse.parse_line(sm.get("line", "")) if isinstance(sm, dict) else None
                            if r:
                                db.add_list(r, "Pantheon")
        # enemy base
        self.bd = bd = boss_defaults(self.cfg_in, level)
        e = enemy.moddb
        for r in ("Fire", "Cold", "Lightning"):
            if f"enemy{r}Resist" not in self.cfg_in:
                e.new(r + "Resist", "BASE", bd["res"], "EnemyDefault")
        if "enemyChaosResist" not in self.cfg_in:
            e.new("ChaosResist", "BASE", bd["chaos"], "EnemyDefault")
        lv = min(max(bd["level"], 1), 100)
        self.enemy_armour = self.cfg_in.get("enemyArmour") or consts()["monsterArmourTable"][lv - 1] * (
            1.0 if bd["boss"] in ("None", "Boss") else 1.0)
        self.enemy_evasion = self.cfg_in.get("enemyEvasion") or consts()["monsterEvasionTable"][lv - 1]
        # config options (PoB's default boss type is Pinnacle)
        apply_config(b, player, enemy, {"enemyIsBoss": "Pinnacle", **self.cfg_in})

        # passive tree
        spec = b.spec()
        for nid, line in spec.stat_lines():
            whole = modparse.parse_line(line.replace("\n", " "))
            parts = [(line, whole)] if whole is not None else [(sub, modparse.parse_line(sub)) for sub in line.split("\n")]
            for sub, r in parts:
                if r is None:
                    self.unparsed.append(("tree", data.nodes()[nid]["name"], sub))
                else:
                    db.add_list(r, "Tree:" + data.nodes()[nid]["name"])
        db.multipliers["AllocatedNotable"] = sum(1 for n in spec.alloc if data.nodes()[n]["type"] == "notable")
        db.multipliers["AllocatedMastery"] = len(spec.masteries)
        db.multipliers["AllocatedLifeMastery"] = sum(1 for n in spec.masteries if data.nodes()[n]["name"] == "Life Mastery")
        # items
        self.weapons = {}
        self.armour_local = {"Armour": 0, "Evasion": 0, "EnergyShield": 0, "Ward": 0}
        self.shield_block = 0
        self.item_list = {}
        use_flasks = bool(self.cfg_in.get("useFlasks"))
        flasks = []
        for slot, text in b.items.items():
            if "Swap" in slot:
                continue
            it, base, mods, weapon, armour, unp = parse_item_mods(text, slot)
            self.item_list[slot] = (it, base)
            self.player.items = getattr(self.player, "items", {})
            self.player.items[slot] = it
            for u in unp:
                self.unparsed.append(("item", slot, u))
            if base.get("type") == "Flask":
                if use_flasks or slot in (getattr(b, "active_flasks", None) or []):
                    flasks.append((slot, it, base, mods))
                continue
            if weapon:
                self.weapons[slot] = weapon
            if armour:
                for k in self.armour_local:
                    self.armour_local[k] += armour.get(k, 0) or 0
                if armour.get("BlockChance"):
                    self.shield_block += armour["BlockChance"]
            for m in mods:
                ok = True
                for t in m.get("tags", []) or []:
                    if t.get("type") in ("SlotNumber", "InSlot"):
                        num = 2 if slot.endswith("2") else 1
                        if t.get("num") != num:
                            ok = False
                if ok:
                    db.add(m, "Item:" + slot)
        self._flasks = flasks
        for nid, text in b.jewels.items():
            if nid not in spec.alloc:
                continue
            if "Cluster Jewel" in itemsmod.parse_item(text)["base"]:
                self.apply_cluster_jewel(text, nid)
                continue
            it, base, mods, _, _, unp = parse_item_mods(text, "Jewel")
            for u in unp:
                self.unparsed.append(("jewel", str(nid), u))
            db.add_list(mods, "Jewel")
        # keystones granted by items / jewels ("Avatar of Fire", "Conduit" ...) -> apply the keystone node's stats
        tree_keys = {data.nodes()[n]["name"] for n in spec.alloc if data.nodes()[n]["type"] == "keystone"}
        for ks in set(v for v in db.list(None, "Keystone") if isinstance(v, str)) - tree_keys:
            node = next((n for n in data.nodes().values() if n["type"] == "keystone" and n["name"] == ks), None)
            if not node:
                self.unparsed.append(("keystone", ks, "keystone not found in tree data"))
                continue
            for st in node.get("stats", []):
                r = modparse.parse_line(st.replace("\n", " "))
                if r is None:
                    self.unparsed.append(("keystone", ks, st))
                else:
                    db.add_list(r, "Keystone:" + ks)
        # anointed notables ("Allocates Longshot")
        anointed = []
        for slot, (it, base) in self.item_list.items():
            for line in itemsmod.active_mod_lines(it):
                mm = __import__("re").match(r"^Allocates (.+)$", line)
                if mm:
                    anointed.append(mm.group(1).strip())
        for nm in anointed:
            node = next((n for n in data.nodes().values() if n["name"] == nm and n["type"] in ("notable", "keystone")
                         and not n.get("ascendancy")), None)
            if node is None:
                continue
            self.unparsed = [u for u in self.unparsed if u[2] != f"Allocates {nm}"]
            for st in node.get("stats", []):
                r = modparse.parse_line(st.replace("\n", " "))
                if r is None:
                    self.unparsed.append(("anoint", nm, st))
                else:
                    db.add_list(r, "Anoint:" + nm)
        self.apply_flasks()
        # weapon conditions
        w1, w2 = self.weapons.get("Weapon 1"), self.weapons.get("Weapon 2")
        it2 = self.item_list.get("Weapon 2")
        if not w1:
            db.conditions["Unarmed"] = True
        else:
            wt = w1["type"]
            db.conditions["Using" + wt.replace(" ", "").replace("OneHanded", "").replace("TwoHanded", "")] = True
            if wt.startswith("Two Handed") or wt in ("Bow", "Staff", "Warstaff", "Fishing Rod"):
                db.conditions["UsingTwoHandedWeapon"] = True
            else:
                db.conditions["UsingOneHandedWeapon"] = True
            if wt in ("Bow",):
                db.conditions["UsingBow"] = True
            if wt in ("Staff", "Warstaff"):
                db.conditions["UsingStaff"] = True
            if wt in ("Wand",):
                db.conditions["UsingWand"] = True
        if it2 and it2[1].get("type") == "Shield":
            db.conditions["UsingShield"] = True
        if w1 and w2:
            db.conditions["DualWielding"] = True
        # local defences into global pool
        for k, v in self.armour_local.items():
            if v:
                db.new(k, "BASE", v, "Item local")
        db.multipliers["Level"] = level

    # -------------------------------------------------------------- flasks
    def apply_flasks(self):
        """Active flasks: base buff + 'during Effect' mods, scaled by flask effect (PoB mergeFlasks)."""
        db = self.player.moddb
        if not self._flasks:
            return
        db.conditions["UsingFlask"] = True
        inc_all = db.sum("INC", {"actor": "player"}, "FlaskEffect")
        inc_magic = db.sum("INC", {"actor": "player"}, "MagicUtilityFlaskEffect")
        type_inc = {"Iron Flask": "IronFlaskEffect", "Granite Flask": "GraniteFlaskEffect", "Ruby Flask": "RubyFlaskEffect",
                    "Sapphire Flask": "SapphireFlaskEffect", "Topaz Flask": "TopazFlaskEffect"}
        seen_bases = set()
        for slot, it, base, mods in self._flasks:
            fl = base.get("flask") or {}
            utility = not (fl.get("life") or fl.get("mana"))
            local = sum(m["value"] for m in mods if m["name"] == "LocalEffect" and m["type"] == "INC")
            inc = inc_all + local
            if it["base"] in type_inc:
                inc += db.sum("INC", {"actor": "player"}, type_inc[it["base"]])
            if it["rarity"] == "MAGIC" and utility:
                inc += inc_magic
            eff = 1 + inc / 100
            buff = []
            if it["base"] not in seen_bases:  # the same flask base's buff doesn't stack
                for line in fl.get("buff") or []:
                    r = modparse.parse_line(line)
                    if r:
                        buff += r
                seen_bases.add(it["base"])
            for m in mods:
                tags = m.get("tags") or []
                if any(t.get("type") == "Condition" and t.get("var") == "UsingFlask" for t in tags):
                    buff.append(dict(m, tags=[t for t in tags if not (t.get("type") == "Condition" and t.get("var") == "UsingFlask")]))
            for m in buff:
                v = m.get("value")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    v = v * eff
                    if m["type"] in ("BASE", "INC") and m["name"] not in ("LifeRegenPercent", "ManaRegenPercent"):
                        v = math.floor(v)
                db.add(dict(m, value=v), f"Flask:{slot}")
            db.conditions["Using" + it["base"].replace(" ", "")] = True

    # -------------------------------------------------------------- cluster jewels
    def apply_cluster_jewel(self, text, nid):
        """Cluster jewel: assume every passive it adds is allocated (how almost all builds use them)."""
        db = self.player.moddb
        it = itemsmod.parse_item(text)
        size = it["base"].split()[0]
        info = data.misc()["clusterJewels"]["jewels"].get(it["base"], {})
        count = info.get("maxNodes", 0)
        notables, small, sockets, other = [], [], 0, []
        for line in itemsmod.active_mod_lines(it):
            line = modparse.roll(line)
            low = line.lower()
            if low.startswith("added small passive skills grant: ") or low.startswith("added small passive skills also grant: "):
                small.append(line.split(": ", 1)[1])
                continue
            r = modparse.parse_line(line)
            if r and r[0]["name"] == "ClusterJewelNotable":
                notables.append(r[0]["value"])
            elif r and r[0]["name"] == "JewelData" and isinstance(r[0]["value"], dict) and \
                    r[0]["value"].get("key") == "clusterJewelNodeCount":
                count = r[0]["value"]["value"]
            elif r and r[0]["name"] == "JewelData" and isinstance(r[0]["value"], dict) and \
                    r[0]["value"].get("key") == "clusterJewelSocketCount":
                sockets = r[0]["value"]["value"]
            elif r and r[0]["name"] in ("JewelData", "ClusterJewelSkill"):
                continue
            elif r is None:
                m = __import__("re").match(r"^(\d+) Added Passive Skills? (?:are|is a) Jewel Sockets?$", line)
                if m:
                    sockets = int(m.group(1))
                else:
                    self.unparsed.append(("cluster", str(nid), line))
            else:
                other += r
        db.add_list(other, f"Cluster:{it['name'] or it['base']}")
        name_to_node = {n["name"]: (k, n) for k, n in data.nodes().items() if n["type"] in ("notable", "keystone") and not n.get("adj")}
        for nm in notables:
            k_n = name_to_node.get(nm)
            if not k_n:
                self.unparsed.append(("cluster", str(nid), f"notable {nm}"))
                continue
            for st in k_n[1].get("stats", []):
                r = modparse.parse_line(st.replace("\n", " "))
                if r is None:
                    self.unparsed.append(("cluster", nm, st))
                else:
                    db.add_list(r, f"Cluster notable:{nm}")
        n_small = max(0, count - len(notables) - sockets)
        for line in small:
            r = modparse.parse_line(line)
            if r is None:
                self.unparsed.append(("cluster", "small passive", line))
                continue
            for m in r:
                if isinstance(m.get("value"), (int, float)) and not isinstance(m.get("value"), bool):
                    m = dict(m, value=m["value"] * n_small)
                db.add(m, f"Cluster small passives x{n_small}")
        self.cluster_points = getattr(self, "cluster_points", 0) + count
        self.notes.append(f"{size} cluster jewel in socket {nid}: assumed all {count} added passives allocated "
                          f"({len(notables)} notables, {n_small} small).")

    # -------------------------------------------------------------- skills
    def build_skill(self, group, gem_index, main_cfg=False):
        """Create an active skill from a socket group's active gem."""
        gems = group.get("gems", [])
        g = gems[gem_index]
        gd = g.get("_gd") or data.find_gem(g["name"])
        if not gd:
            return None
        sk = skills_data().get(gd["skillId"])
        if not sk:
            return None
        slot = group.get("slot")
        db = self.player.moddb
        # gem level / quality boosts (items, global mods)
        level, quality = self.gem_boosts(gd, slot, g.get("level", 20), g.get("quality", 0))
        types = set(sk.get("skillTypes", []))
        supports = []
        # socketed supports
        for i, sg in enumerate(gems):
            if i == gem_index or not sg.get("enabled", True):
                continue
            sd = sg.get("_gd") or data.find_gem(sg["name"])
            if not sd:
                continue
            lv_q = (sg.get("level", 20), sg.get("quality", 0))
            if sd["support"]:
                supports.append((sd, *lv_q))
            sec = sd.get("secondarySkillId")
            if sec and (skills_data().get(sec) or {}).get("support"):
                # e.g. Cast On Critical Strike's triggered-spell part, Spellslinger / Arcanist Brand
                supports.append(({**sd, "skillId": sec, "support": True}, *lv_q))
        # item-granted supports (e.g. "Socketed Gems are Supported by Level 20 X")
        for v in db.list({"slotName": slot, "gem": gd}, "ExtraSupport"):
            if isinstance(v, dict) and v.get("skillId"):
                ssk = skills_data().get(v["skillId"])
                if ssk:
                    sd = next((x for x in data.gems().values() if x.get("skillId") == v["skillId"]), None)
                    supports.append((sd or {"name": ssk["name"], "skillId": v["skillId"], "support": True},
                                     v.get("level", 1), 0))
        # support compatibility + added types
        from .skills import support_fits
        active_view = {"name": gd["name"], "skillTypes": sorted(types)}
        used = []
        trig = None
        for sd, lv, q in supports:
            ssk = skills_data().get(sd.get("skillId"))
            if not ssk:
                continue
            ok, _ = support_fits({**ssk, "name": ssk["name"]}, active_view)
            if ok:
                used.append((sd, ssk, lv, q))
                types |= set(ssk.get("addSkillTypes", []))
                active_view["skillTypes"] = sorted(types)
                if ssk.get("isTrigger") or "Triggered" in (ssk.get("addSkillTypes") or []):
                    trig = (ssk, lv, q)
        mana_mult = 100.0
        sup_mods = []
        rows = []
        for sd, ssk, lv, q in used:
            if sd.get("gemId"):
                lv, q = self.gem_boosts(sd, slot, lv, q)
            smods, srow, _, _ = skill_instance_mods(ssk, lv, q, "Support:" + ssk["name"])
            # Empower / Enhance style supports raise the supported gem's level / quality
            for m in smods:
                if m["name"] == "SupportedGemProperty" and isinstance(m["value"], dict):
                    v = m["value"]
                    if gem_is_type(gd, v.get("keyword", "all")):
                        if v.get("key") == "level":
                            level += v.get("value") or 0
                        elif v.get("key") == "quality":
                            quality += v.get("value") or 0
            sup_mods += [m for m in smods if m["name"] != "SupportedGemProperty"]
            rows.append(srow)
        if sk.get("levels"):
            level = max(1, min(level, max(int(k) for k in sk["levels"])))
        mods, row, stats, unmapped = skill_instance_mods(sk, level, quality, "Skill:" + gd["name"])
        mods += sup_mods
        for srow in rows:
            if srow.get("manaMultiplier"):
                mana_mult *= (100 + srow["manaMultiplier"]) / 100
        skill = {"name": gd["name"], "gem": gd, "sk": sk, "skillId": gd["skillId"], "types": types, "slot": slot,
                 "level": level, "quality": quality, "row": row, "stats": stats, "supports": [u[1]["name"] for u in used],
                 "manaMult": mana_mult, "trigger": trig, "group": group}
        # split global effects (buffs/curses/auras) from skill-local mods
        local, glob = [], []
        for m in mods:
            ge = next((t for t in m.get("tags", []) if t.get("type") == "GlobalEffect"), None)
            (glob if ge else local).append(m)
        skill["globalMods"] = glob
        sdb = ModDB(self.player, parent=db)
        sdb.add_list([m for m in local if m["name"] != "SkillData"])
        skill_data = {}
        for m in local:
            if m["name"] == "SkillData" and isinstance(m["value"], dict):
                skill_data[m["value"]["key"]] = m["value"].get("value")
        for k in ("critChance", "damageEffectiveness", "baseMultiplier", "attackSpeedMultiplier", "cooldown",
                  "storedUses", "manaReservationPercent", "manaReservationFlat", "lifeReservationPercent",
                  "lifeReservationFlat", "duration"):
            if row.get(k) is not None:
                skill_data.setdefault(k, row[k])
        skill["data"] = skill_data
        skill["db"] = sdb
        flags = set(sk.get("baseFlags", [])) | set(sk.get("addFlags", []))
        for sd, ssk, lv, q in used:
            flags |= set(ssk.get("addFlags", []))
        if "Attack" in types:
            flags.add("attack")
        if "Spell" in types and "attack" not in flags:
            flags.add("spell")
        if "Projectile" in types:
            flags.add("projectile")
        if "Area" in types:
            flags.add("area")
        if "Melee" in types:
            flags.add("melee")
        flags.add("hit") if ("Damage" in types or "Attack" in types or "Projectile" in types) else None
        if sdb.flag(None, "CannotDealDamage") or "base_deal_no_damage" in stats:
            flags.discard("hit")
        skill["flags"] = flags
        return skill

    def gem_boosts(self, gd, slot, level, quality):
        """Apply GemProperty mods (+levels / +quality from items and global modifiers)."""
        db = self.player.moddb
        for v in db.list({"slotName": slot, "gem": gd}, "GemProperty"):
            if not isinstance(v, dict):
                continue
            kws = v.get("keywordList") or [v.get("keyword", "all")]
            if all(gem_is_type(gd, k) for k in kws):
                if v.get("key") == "level":
                    level += v.get("value", 0) or 0
                elif v.get("key") == "quality":
                    quality += v.get("value", 0) or 0
        return level, quality

    def _cfg(self, skill, mode="hit", dmg_types=()):
        f = 0
        kw = 0
        fl = skill["flags"]
        t = skill["types"]
        if "attack" in fl:
            f |= MF("Attack")
            kw |= KF("Attack")
        if "spell" in fl:
            f |= MF("Spell")
            kw |= KF("Spell")
        if "melee" in fl:
            f |= MF("Melee")
        if "area" in fl:
            f |= MF("Area")
        if "projectile" in fl:
            f |= MF("Projectile")
        for typ, k in (("Aura", "Aura"), ("AppliesCurse", "Curse"), ("Warcry", "Warcry"), ("Movement", "Movement"),
                       ("Vaal", "Vaal"), ("Trapped", "Trap"), ("RemoteMined", "Mine"), ("SummonsTotem", "Totem"),
                       ("Brand", "Brand"), ("Minion", "Minion")):
            if typ in t:
                kw |= KF(k)
        if "Attack" in t and self.weapons.get("Weapon 1", {}).get("type") == "Bow":
            kw |= KF("Bow")
            if "Projectile" in t:
                kw |= KF("Arrow")
        if mode == "hit":
            f |= MF("Hit")
            kw |= KF("Hit")
            if "melee" in fl:
                f |= MF("MeleeHit")
        elif mode == "dot":
            f |= MF("Dot")
        elif mode in ("ignite", "bleed", "poison"):
            f |= MF("Dot") | MF("Ailment")
            kw |= KF("Ailment") | KF({"ignite": "Ignite", "bleed": "Bleed", "poison": "Poison"}[mode])
        if "attack" in fl:
            w = self.weapons.get(skill.get("hand", "Weapon 1"))
            if w:
                f |= MF("Weapon")
                wt = w["type"]
                for key, flag in (("Axe", "Axe"), ("Bow", "Bow"), ("Claw", "Claw"), ("Dagger", "Dagger"),
                                  ("Mace", "Mace"), ("Sceptre", "Mace"), ("Staff", "Staff"), ("Sword", "Sword"),
                                  ("Wand", "Wand"), ("Fishing", "Fishing")):
                    if key in wt:
                        f |= MF(flag)
                if wt.startswith("Two Handed") or wt in ("Staff", "Warstaff", "Bow", "Fishing Rod"):
                    f |= MF("Weapon2H")
                else:
                    f |= MF("Weapon1H")
                f |= MF("WeaponRanged") if wt in ("Bow", "Wand") else MF("WeaponMelee")
            else:
                f |= MF("Unarmed")
        for d in dmg_types:
            kw |= KF(d)
        return {"flags": f, "kw": kw, "skillName": skill["name"], "skillId": skill["skillId"],
                "skillTypes": skill["types"], "slotName": skill["slot"], "baseFlags": skill["flags"],
                "gem": skill["gem"], "skillPart": skill.get("part", 1),
                "skillDist": float(self.cfg_in.get("projectileDistance") or 40) if "projectile" in fl
                else float(self.cfg_in.get("meleeDistance") or 15),
                "skillCond": {}}

    def extra_skill_groups(self):
        """Skills granted by passives/items ('Grants Level 20 Summon Spectral Tiger'), like PoB's auto groups."""
        out = []
        for v in self.player.moddb.list(None, "ExtraSkill"):
            if not isinstance(v, dict) or not v.get("skillId") or v.get("triggered"):
                continue
            sk = skills_data().get(v["skillId"])
            if not sk or sk.get("support"):
                continue
            gd = next((g for g in data.gems().values() if g.get("skillId") == v["skillId"]), None) or {
                "name": sk["name"], "skillId": v["skillId"], "support": False, "req": {},
                "tagList": [t.lower() for t in sk.get("skillTypes", [])]}
            out.append({"slot": None, "enabled": True, "source": "granted",
                        "gems": [{"name": sk["name"], "level": v.get("level", 1), "quality": 0, "enabled": True, "_gd": gd}]})
        return out

    def _reserve(self, sk, cfg, reserved):
        sdb, sd = sk["db"], sk["data"]
        mult = math.floor(sk["manaMult"] / 100 * 10000) / 10000
        pools = {"Mana": [sd.get("manaReservationFlat") or 0, sd.get("manaReservationPercent") or 0],
                 "Life": [sd.get("lifeReservationFlat") or 0, sd.get("lifeReservationPercent") or 0]}
        if sdb.flag(cfg, "BloodMagicReserved"):
            pools["Life"] = [pools["Life"][0] + pools["Mana"][0], pools["Life"][1] + pools["Mana"][1]]
            pools["Mana"] = [0, 0]
        for name, (bflat, bpct) in pools.items():
            if not (bflat or bpct):
                continue
            rmore = sdb.more(cfg, name + "Reserved")
            gmore = sdb.more(cfg, "Reserved")
            inc = sdb.sum("INC", cfg, name + "Reserved", "Reserved")
            eff = max(sdb.sum("INC", cfg, name + "ReservationEfficiency", "ReservationEfficiency"), -100)
            effm = sdb.more(cfg, name + "ReservationEfficiency", "ReservationEfficiency")

            def stage(v):
                v = v + math.trunc(v * inc / 100)
                v = v + math.trunc(v * (rmore - 1))
                return v + math.trunc(v * (gmore - 1))
            if bflat:
                bv = math.trunc(bflat * mult)
                if bv:
                    reserved[name] += max(rnd(stage(bv) / (1 + eff / 100) / effm), 0)
            if bpct:
                bv = math.trunc(bpct * 100 * mult)
                if bv:
                    reserved[name + "Percent"] += max(rnd(stage(bv) / (1 + eff / 100) / effm) / 100, 0)

    # -------------------------------------------------------------- triggers
    def trigger_rate(self, skill, sdb, cfg):
        """Approximate PoB trigger maths: min(source rate, 1 / adjusted cooldown) shared between triggered spells."""
        ssk, lv, q = skill["trigger"]
        stats, row = gem_stats(ssk, lv, q)
        # the gem's other granted effect (e.g. Cast while Channelling's channel part) holds trigger timings
        for gd in data.gems().values():
            if gd.get("secondarySkillId") == skills_id(ssk) and gd.get("skillId") in skills_data():
                pst, prow = gem_stats(skills_data()[gd["skillId"]], lv, q)
                stats = {**pst, **stats}
                break
        cdr = 1 + sdb.sum("INC", cfg, "CooldownRecovery") / 100
        cd = (row.get("cooldown") or skill["data"].get("cooldown") or 0) / cdr if (row.get("cooldown") or skill["data"].get("cooldown")) else 0
        # PoB rounds trigger cooldowns up to server ticks (33 ms)
        if cd:
            tick = 0.033
            cd = math.ceil(cd / tick - 1e-9) * tick
        info = {"by": ssk["name"], "cooldown": rnd(cd, 3) if cd else None}
        group = skill.get("group") or {}
        triggered_in_group = 0
        attacks = []
        for i, g in enumerate(group.get("gems", [])):
            gd = g.get("_gd") or data.find_gem(g["name"])
            if not gd or gd["support"] or not g.get("enabled", True):
                continue
            sk2 = skills_data().get(gd["skillId"], {})
            if "Attack" in sk2.get("skillTypes", []):
                attacks.append(i)
            elif "Spell" in sk2.get("skillTypes", []):
                triggered_in_group += 1
        triggered_in_group = max(1, triggered_in_group)
        source = None
        if "cast_spell_on_linked_attack_crit" in stats or "Crit" in ssk["name"]:
            if attacks:
                src = self.build_skill(group, attacks[0])
                src["trigger"] = None
                o = self.calc_offence_core(src)
                source = o["speed"] * o["crit_chance"] / 100 * o["hit_chance"] / 100
                info["source"] = f"{src['name']} crits: {rnd(o['speed'], 2)}/s x {rnd(o['crit_chance'], 1)}% crit"
            else:
                info["note"] = "no linked attack to trigger from"
                source = 0
        elif skill["data"].get("triggerTime") or stats.get("cast_while_channelling_time_ms"):
            tt = skill["data"].get("triggerTime") or stats["cast_while_channelling_time_ms"] / 1000
            source = 1 / tt if tt else None
            info["source"] = f"every {tt:.2f}s while channelling"
        elif "base_skill_show_average_damage_instead_of_dps" in stats or "cast_on_damage_taken_threshold" in stats:
            info["note"] = "triggered by taking damage / dying: average hit only, no sustained DPS"
            info["rate"] = None
            return info
        rate_cap = 1 / cd if cd else None
        cands = [x for x in (source, rate_cap) if x is not None]
        rate = min(cands) if cands else None
        if rate is not None:
            rate /= triggered_in_group
        info["rate"] = rnd(rate, 3) if rate is not None else None
        if triggered_in_group > 1:
            info["shared_between"] = triggered_in_group
        self.notes.append(f"{skill['name']} is triggered by {ssk['name']} (~{info['rate']}/s, approximate trigger maths).")
        return info

    # -------------------------------------------------------------- buffs
    def apply_buffs(self, main_skill):
        b = self.build
        db, edb = self.player.moddb, self.enemy.moddb
        curses = []
        self.buffs = []
        self.ally_buffs = []
        # count heralds / auras affecting you (PoB CalcPerform)
        heralds, auras = set(), set()
        for grp in list(b.skill_groups) + self.extra_skill_groups():
            if not grp.get("enabled", True) or "Swap" in (grp.get("slot") or ""):
                continue
            for g in grp.get("gems", []):
                gd = g.get("_gd") or data.find_gem(g["name"])
                if not gd or gd["support"] or not g.get("enabled", True):
                    continue
                st = set(skills_data().get(gd["skillId"], {}).get("skillTypes", []))
                if "Herald" in st:
                    heralds.add(gd["name"])
                elif "Aura" in st and "AuraAffectsEnemies" not in st:
                    auras.add(gd["name"])
        if heralds:
            db.multipliers["Herald"] = len(heralds)
            db.conditions["AffectedByHerald"] = True
        if auras:
            db.multipliers["AuraAffectingSelf"] = len(auras)
        reserved = {"Mana": 0, "Life": 0, "ManaPercent": 0.0, "LifePercent": 0.0}
        for gi, grp in enumerate(list(b.skill_groups) + self.extra_skill_groups(), 1):
            if not grp.get("enabled", True) or "Swap" in (grp.get("slot") or ""):
                continue  # PoB: gems in the inactive weapon set do nothing
            for i, g in enumerate(grp.get("gems", [])):
                if not g.get("enabled", True):
                    continue
                gd = g.get("_gd") or data.find_gem(g["name"])
                if not gd or gd["support"]:
                    continue
                sk = self.build_skill(grp, i)
                if not sk:
                    continue
                cfg = self._cfg(sk, "buff")
                # reservation (PoB CalcPerform, integer stages)
                self._reserve(sk, cfg, reserved)
                if sk is main_skill and not sk["globalMods"]:
                    continue
                if not sk["globalMods"]:
                    continue
                kinds = {t.get("effectType") for m in sk["globalMods"] for t in m.get("tags", []) if t.get("type") == "GlobalEffect"}
                is_curse = bool(kinds & {"Curse"}) or "AppliesCurse" in sk["types"]
                if is_curse:
                    curses.append(sk)
                    continue
                inc = sk["db"].sum("INC", cfg, "BuffEffect", "BuffEffectOnSelf")
                if "Aura" in kinds or "Aura" in sk["types"]:
                    inc += sk["db"].sum("INC", cfg, "AuraEffect", "AuraEffectOnSelf")
                mult = (1 + inc / 100) * sk["db"].more(cfg, "BuffEffect", "AuraEffect")
                for m in sk["globalMods"]:
                    gt = next(t for t in m.get("tags", []) if t.get("type") == "GlobalEffect")
                    if gt.get("effectType") in ("Debuff", "Curse"):
                        tgt = edb
                    else:
                        tgt = db
                    nm = dict(m)
                    nm["tags"] = [t for t in m.get("tags", []) if t.get("type") != "GlobalEffect"]
                    if isinstance(nm["value"], (int, float)) and not isinstance(nm["value"], bool) and not gt.get("unscalable"):
                        nm["value"] = nm["value"] * mult
                    nm["source"] = "Buff:" + sk["name"]
                    tgt.add(nm)
                    if gt.get("effectType") == "Aura" and tgt is db:
                        self.ally_buffs.append(nm)
                self.buffs.append(f"{sk['name']} (+{inc:.0f}% effect)")
                db.conditions["AffectedBy" + sk["name"].replace(" ", "").replace("'", "")] = True
        limit = int(db.sum("BASE", None, "EnemyCurseLimit"))
        self.curses = []
        for sk in curses[:max(limit, 0)]:
            cfg = self._cfg(sk, "buff")
            inc = sk["db"].sum("INC", cfg, "CurseEffect") + edb.sum("INC", None, "CurseEffectOnSelf")
            mult = (1 + inc / 100) * sk["db"].more(cfg, "CurseEffect") * edb.more(None, "CurseEffectOnSelf")
            for m in sk["globalMods"]:
                nm = dict(m)
                nm["tags"] = [t for t in m.get("tags", []) if t.get("type") != "GlobalEffect"]
                if isinstance(nm["value"], (int, float)) and not isinstance(nm["value"], bool):
                    nm["value"] = nm["value"] * mult
                nm["source"] = "Curse:" + sk["name"]
                edb.add(nm)
            self.curses.append(sk["name"])
        self.reserved = reserved
        # enemy modifiers granted by the player ("Nearby Enemies have ...")
        for v in db.list(None, "EnemyModifier"):
            if isinstance(v, dict) and isinstance(v.get("mod"), dict):
                edb.add(norm_mod(v["mod"], "EnemyModifier"))
        # exposure: only the strongest applies (PoB CalcPerform)
        for el in ("Fire", "Cold", "Lightning"):
            vals = [r["value"] for r in edb.tabulate("BASE", None, el + "Exposure")]
            if not vals:
                continue
            v = min(vals) + db.sum("BASE", None, "ExtraExposure", "Extra" + el + "Exposure")
            inc = db.sum("INC", None, "ExposureEffect", el + "ExposureEffect")
            v *= 1 + inc / 100
            edb.conditions["Has" + el + "Exposure"] = True
            edb.new(el + "Resist", "BASE", v, "~Exposure")
            db.conditions["AppliedExposureRecently"] = True

    # -------------------------------------------------------------- defence
    def calc_defence(self):
        db = self.player.moddb
        out = self.player.output
        # drop mods derived by a previous pass (attribute bonuses, resist conversions, ...)
        for name in list(db.mods):
            db.mods[name] = [m for m in db.mods[name] if not str(m.get("source", "")).startswith("~")]
        # charges
        for ch in ("Power", "Frenzy", "Endurance"):
            mx = db.sum("BASE", None, ch + "ChargesMax")
            out[ch + "ChargesMax"] = mx
            use = db.flag(None, "Use" + ch + "Charges")
            ov = db.override(None, ch + "Charges")
            n = ov if ov is not None else (mx if use else 0)
            db.multipliers[ch + "Charge"] = n
            out[ch + "Charges"] = n
        db.multipliers["Rage"] = db.get_multiplier("RageStack", None)
        # attributes (two passes for attribute-dependent attribute mods)
        for _ in range(2):
            for st in ("Str", "Dex", "Int"):
                base = db.sum("BASE", None, st) + db.sum("BASE", None, "AllAttributes")
                inc = db.sum("INC", None, st, "AllAttributes")
                out[st] = math.floor(base * (1 + inc / 100) * db.more(None, st, "AllAttributes"))
            out["LowestAttribute"] = min(out["Str"], out["Dex"], out["Int"])
        s, d, i = out["Str"], out["Dex"], out["Int"]
        # attribute bonuses (PoB CalcPerform)
        if not db.flag(None, "NoAttributeBonuses"):
            if not db.flag(None, "NoStrengthAttributeBonuses"):
                if not db.flag(None, "NoStrBonusToLife"):
                    db.new("Life", "BASE", math.floor(s / 2), "~Strength")
                ratio = db.sum("BASE", None, "StrDmgBonusRatioOverride")
                extra = db.sum("BASE", None, "DexIntToMeleeBonus")
                bonus = math.floor((s + extra) * ratio) if ratio > 0 else math.floor((s + extra) / 5)
                db.new("PhysicalDamage", "INC", bonus, "~Strength", MF("Melee"))
            if not db.flag(None, "NoDexterityAttributeBonuses"):
                acc = db.override(None, "DexAccBonusOverride")
                db.new("Accuracy", "BASE", d * (acc if acc is not None else 2), "~Dexterity")
                if not db.flag(None, "NoDexBonusToEvasion"):
                    db.new("Evasion", "INC", math.floor(d / 5), "~Dexterity")
            if not db.flag(None, "NoIntelligenceAttributeBonuses"):
                if not db.flag(None, "NoIntBonusToMana"):
                    db.new("Mana", "BASE", math.floor(i / 2), "~Intelligence")
                if not db.flag(None, "NoIntBonusToES"):
                    db.new("EnergyShield", "INC", math.floor(i / 10), "~Intelligence")
        # pools
        ci = db.flag(None, "ChaosInoculation")
        life = db.sum("BASE", None, "Life") * (1 + db.sum("INC", None, "Life") / 100) * db.more(None, "Life")
        ov = db.override(None, "Life")
        if ci:
            life = 1
        elif ov is not None:
            life = ov
        out["Life"] = max(1, rnd(life))
        out["Mana"] = max(0, rnd(db.sum("BASE", None, "Mana") * (1 + db.sum("INC", None, "Mana") / 100) * db.more(None, "Mana")))
        es_inc = db.sum("INC", None, "EnergyShield", "Defences", "ArmourAndEnergyShield", "EvasionAndEnergyShield")
        out["EnergyShield"] = max(0, rnd(db.sum("BASE", None, "EnergyShield") * (1 + es_inc / 100) * db.more(None, "EnergyShield", "Defences")))
        gain = db.sum("BASE", None, "LifeGainAsEnergyShield")
        if gain and not ci:
            out["EnergyShield"] += rnd(out["Life"] * gain / 100)
        gain = db.sum("BASE", None, "ManaGainAsEnergyShield")
        if gain:
            out["EnergyShield"] += rnd(out["Mana"] * gain / 100)
        self.calc_resists_block(ci)
        # PoB: armour/evasion increased by fire/cold resistance (Chieftain, etc.)
        for flag, name, key in (("ArmourIncreasedByUncappedFireRes", "Armour", "FireResistTotal"),
                                ("ArmourIncreasedByOvercappedFireRes", "Armour", "FireResistOverCap"),
                                ("EvasionRatingIncreasedByUncappedColdRes", "Evasion", "ColdResistTotal"),
                                ("EvasionRatingIncreasedByOvercappedColdRes", "Evasion", "ColdResistOverCap")):
            if db.flag(None, flag):
                db.new(name, "INC", out[key], "~" + flag)
        arm_inc = db.sum("INC", None, "Armour", "ArmourAndEvasion", "ArmourAndEnergyShield", "Defences")
        out["Armour"] = max(0, rnd(db.sum("BASE", None, "Armour", "ArmourAndEvasion") * (1 + arm_inc / 100) * db.more(None, "Armour", "Defences")))
        eva_inc = db.sum("INC", None, "Evasion", "ArmourAndEvasion", "EvasionAndEnergyShield", "Defences")
        eva = db.sum("BASE", None, "Evasion", "ArmourAndEvasion") * (1 + eva_inc / 100) * db.more(None, "Evasion", "Defences")
        if db.flag(None, "IronReflexes"):
            out["Armour"] += rnd(eva)
            eva = 0
        out["Evasion"] = max(0, rnd(eva))
        out["Ward"] = max(0, rnd(db.sum("BASE", None, "Ward") * (1 + db.sum("INC", None, "Ward", "Defences") / 100) * db.more(None, "Ward")))
        # reservation
        r = getattr(self, "reserved", {"ManaPercent": 0, "Mana": 0})
        for pool in ("Mana", "Life"):
            mx = out[pool]
            res = min(mx, r.get(pool, 0) + math.ceil(mx * r.get(pool + "Percent", 0) / 100))
            out[pool + "Reserved"] = res
            out[pool + "ReservedPercent"] = rnd(res / mx * 100, 1) if mx else 0
            out[pool + "Unreserved"] = mx - res
        out["SpellSuppressionChance"] = min(db.sum("BASE", None, "SpellSuppressionChance") * (1 + db.sum("INC", None, "SpellSuppressionChance") / 100), 100)
        out["SpellSuppressionEffect"] = 40 + db.sum("BASE", None, "SpellSuppressionEffect")
        # regen / recharge
        life_regen = db.sum("BASE", None, "LifeRegen") + out["Life"] * db.sum("BASE", None, "LifeRegenPercent") / 100
        out["LifeRegen"] = rnd(life_regen * (1 + db.sum("INC", None, "LifeRegen", "LifeRecoveryRate") / 100) * db.more(None, "LifeRegen"), 1)
        mana_regen = out["Mana"] * 0.0175 + db.sum("BASE", None, "ManaRegen") + out["Mana"] * db.sum("BASE", None, "ManaRegenPercent") / 100
        out["ManaRegen"] = rnd(mana_regen * (1 + db.sum("INC", None, "ManaRegen", "ManaRecoveryRate") / 100) * db.more(None, "ManaRegen"), 1)
        out["EnergyShieldRecharge"] = rnd(out["EnergyShield"] * 0.33 * (1 + db.sum("INC", None, "EnergyShieldRecharge") / 100), 1)
        tc = max(-75, db.sum("INC", None, "TemporalChainsActionSpeed"))
        out["ActionSpeedMod"] = max(0, 1 + (tc + db.sum("INC", None, "ActionSpeed")) / 100)
        out["MovementSpeedMod"] = rnd((1 + db.sum("INC", None, "MovementSpeed") / 100) * db.more(None, "MovementSpeed")
                                      * out["ActionSpeedMod"], 4)
        # armour penalty from body armour
        out["Accuracy"] = rnd(db.sum("BASE", {"flags": MF("Attack")}, "Accuracy") * (1 + db.sum("INC", {"flags": MF("Attack")}, "Accuracy") / 100) * db.more({"flags": MF("Attack")}, "Accuracy"))
        out["PhysicalDamageReduction"] = min(db.sum("BASE", None, "PhysicalDamageReduction"), 90)
        # max hit taken (PoB CalcDefence: pools / (resist * reduction * taken) / enemy damage mult)
        edb = self.enemy.moddb
        ci = db.flag(None, "ChaosInoculation")
        life, es = out["Life"], out["EnergyShield"]
        mom = min(db.sum("BASE", None, "DamageTakenFromManaBeforeLife"), 100)
        mana_pool = out.get("ManaUnreserved", out["Mana"]) if mom else 0
        armour = out["Armour"]
        dr_max = 90
        bd = getattr(self, "bd", {"pen": 0})
        cfgi = getattr(self, "cfg_in", {})
        for el in ("Physical", "Fire", "Cold", "Lightning", "Chaos"):
            is_ele = el in ELEMENTAL
            if el == "Physical":
                resist = 0
            else:
                resist = out[el + "Resist"]
            pen = float(cfgi.get(f"enemy{el}Pen", bd.get("pen", 0) if is_ele else 0) or 0)
            res_mult = 1 - (resist - pen) / 100
            red_names = [el + "DamageReduction"] + (["ElementalDamageReduction"] if is_ele else [])
            ov = db.override(None, el + "DamageReduction")
            reduction = ov if ov is not None else db.sum("BASE", None, *red_names)
            reduction = min(reduction, dr_max)
            overwhelm = float(cfgi.get("enemyPhysicalOverwhelm", 0) or 0) if el == "Physical" else 0
            if el == "Physical":
                armour_pct = 0 if db.flag(None, "ArmourDoesNotApplyToPhysicalDamageTaken") else 100
            else:
                armour_pct = min(db.sum("BASE", None, "ArmourAppliesTo" + el + "DamageTaken"), 100)
            eff_armour = armour * armour_pct / 100
            tnames = ["DamageTaken", el + "DamageTaken", "DamageTakenWhenHit", el + "DamageTakenWhenHit"] + \
                (["ElementalDamageTaken"] if is_ele else [])
            taken = max((1 + db.sum("INC", None, *tnames) / 100) * db.more(None, *tnames), 0)
            enames = ["Damage", el + "Damage"] + (["ElementalDamage"] if is_ele else [])
            enemy_mult = (1 + edb.sum("INC", None, *enames) / 100) * edb.more(None, *enames)
            pool = life + (es if (el != "Chaos" or ci or db.flag(None, "ChaosNotBypassEnergyShield")
                                  or db.flag(None, "EnergyShieldProtectsFromChaos")) else 0)
            if ci:
                pool = es + (0 if el == "Chaos" else 1)
            if mom:
                pool = min(pool / (1 - mom / 100), pool + mana_pool) if mom < 100 else pool + mana_pool

            def taken_from(raw):
                ar = eff_armour / (eff_armour + 5 * raw * res_mult) * 100 if eff_armour > 0 and raw > 0 else 0
                tot = min(dr_max, min(dr_max, ar) + reduction)
                red_mult = 1 - max(min(dr_max, tot - overwhelm), 0) / 100
                return raw * res_mult * red_mult * taken
            if el == "Chaos" and ci:
                out[el + "MaximumHitTaken"] = float("inf")
                continue
            if taken_from(1e9) <= 0:
                out[el + "MaximumHitTaken"] = float("inf")
                continue
            if eff_armour > 0:
                lo, hi = 0.0, 1.0
                while taken_from(hi) < pool:
                    hi *= 2
                for _ in range(80):
                    mid = (lo + hi) / 2
                    if taken_from(mid) > pool:
                        hi = mid
                    else:
                        lo = mid
                raw = lo
            else:
                raw = pool / taken_from(1.0)
            out[el + "MaximumHitTaken"] = rnd(raw / enemy_mult) if enemy_mult > 0 else float("inf")
        pool = life + es
        out["TotalEHPPool"] = pool

    def calc_resists_block(self, ci=False):
        """PoB calcs.resistances + block (CalcDefence.lua)."""
        db, out = self.player.moddb, self.player.output
        types = ("Fire", "Cold", "Lightning", "Chaos")
        # resistance / max resistance conversion (e.g. "Fire Resistance also applies to Cold")
        for kind, suffix in (("MaxResConvertTo", "ResistMax"), ("ResConvertTo", "Resist")):
            for src in types:
                for dst in types:
                    rate = db.sum("BASE", None, f"{src}{kind}{dst}") / 100
                    if not rate:
                        continue
                    v = sum(r["value"] for r in db.tabulate("BASE", None, src + suffix)
                            if r["source"] != "Base" and not str(r["source"]).startswith("~"))
                    if v:
                        db.new(dst + suffix, "BASE", v * rate, f"~{src} to {dst} conversion")
                    if suffix == "Resist":
                        for r in db.tabulate("INC", None, src + "Resist"):
                            if not str(r["source"]).startswith("~"):
                                db.new(dst + "Resist", "INC", r["value"] * rate, "~" + str(r["source"]))
        if db.flag(None, "ElementalResistMaxIsHighestResistMax"):
            hi = max(min(90, db.sum("BASE", None, e + "ResistMax", "ElementalResistMax")) for e in ("Fire", "Cold", "Lightning"))
            for e in ("Fire", "Cold", "Lightning"):
                db.new(e + "ResistMax", "OVERRIDE", hi, "Melding of the Flesh")
        for el in types:
            ele = el != "Chaos"
            names = [el + "Resist"] + (["ElementalResist"] if ele else [])
            mx = db.override(None, el + "ResistMax")
            if mx is None:
                mx = min(90, db.sum("BASE", None, el + "ResistMax", *(["ElementalResistMax"] if ele else [])))
            total = db.override(None, el + "Resist")
            if total is None:
                base = db.sum("BASE", None, *names)
                inc = max((1 + db.sum("INC", None, *names) / 100) * db.more(None, *names), 0)
                total = base * inc
            if el == "Chaos" and ci:
                total = 100
            total = math.trunc(total)
            mx = math.trunc(mx)
            out[el + "ResistTotal"] = total
            out[el + "ResistMax"] = mx
            out[el + "Resist"] = max(min(total, mx), -200)
            out[el + "ResistOverCap"] = max(0, total - mx)
            out[el + "ResistOver75"] = max(0, out[el + "Resist"] - 75)
        # block
        bmax = min(db.sum("BASE", None, "BlockChanceMax"), 90)
        inc = db.sum("INC", None, "BlockChance")
        more = db.more(None, "BlockChance")
        if db.flag(None, "MaxBlockIfNotBlockedRecently"):
            out["BlockChance"] = bmax
        else:
            tot = rnd((getattr(self, "shield_block", 0) + db.sum("BASE", None, "BlockChance")) * (1 + inc / 100) * more)
            out["BlockChance"] = min(tot, bmax)
        smax = bmax if db.flag(None, "SpellBlockChanceMaxIsBlockChanceMax") else min(db.sum("BASE", None, "SpellBlockChanceMax"), 90)
        if db.flag(None, "MaxSpellBlockIfNotBlockedRecently"):
            out["SpellBlockChance"] = smax
        elif db.flag(None, "SpellBlockChanceIsBlockChance"):
            out["SpellBlockChance"] = out["BlockChance"]
        else:
            out["SpellBlockChance"] = min(rnd(db.sum("BASE", None, "SpellBlockChance") * (1 + inc / 100) * more), smax)
        if db.flag(None, "CannotBlockAttacks"):
            out["BlockChance"] = 0
        if db.flag(None, "CannotBlockSpells"):
            out["SpellBlockChance"] = 0

    # -------------------------------------------------------------- offence
    def _conversion(self, sdb, cfg):
        """PoB-style conversion table: conv[src][dst] fraction, gain[src][dst] fraction."""
        conv = {s: {} for s in DMG_TYPES}
        gain = {s: {} for s in DMG_TYPES}
        for i, src in enumerate(DMG_TYPES):
            later = DMG_TYPES[i + 1:]
            skill_conv = {}
            glob_conv = {}
            for dst in later:
                names_s = [f"Skill{src}DamageConvertTo{dst}"]
                names_g = [f"{src}DamageConvertTo{dst}"]
                if src in ELEMENTAL:
                    names_s.append(f"SkillElementalDamageConvertTo{dst}")
                    names_g.append(f"ElementalDamageConvertTo{dst}")
                if src != "Chaos":
                    names_g.append(f"NonChaosDamageConvertTo{dst}")
                skill_conv[dst] = sdb.sum("BASE", cfg, *names_s)
                glob_conv[dst] = sdb.sum("BASE", cfg, *names_g)
            ts = sum(skill_conv.values())
            if ts > 100:
                for k in skill_conv:
                    skill_conv[k] *= 100 / ts
                ts = 100
            tg = sum(glob_conv.values())
            if tg + ts > 100 and tg > 0:
                for k in glob_conv:
                    glob_conv[k] *= (100 - ts) / tg
            for dst in later:
                c = (skill_conv[dst] + glob_conv[dst]) / 100
                if c:
                    conv[src][dst] = c
            for dst in DMG_TYPES:
                if dst == src:
                    continue
                names = [f"{src}DamageGainAs{dst}"]
                if src in ELEMENTAL:
                    names.append(f"ElementalDamageGainAs{dst}")
                if src != "Chaos":
                    names.append(f"NonChaosDamageGainAs{dst}")
                v = sdb.sum("BASE", cfg, *names)
                if v:
                    gain[src][dst] = v / 100
        return conv, gain

    def _flow(self, base, conv, gain):
        """Distribute base damage through conversion; returns {dst: {chain(tuple): (min,max)}}."""
        buckets = {d: {} for d in DMG_TYPES}
        for d in DMG_TYPES:
            mn, mx = base.get(d, (0, 0))
            if mn or mx:
                buckets[d][(d,)] = (mn, mx)
        for src in DMG_TYPES:
            for chain, (mn, mx) in list(buckets[src].items()):
                total_conv = sum(conv[src].values())
                keep = max(0, 1 - total_conv)
                for dst, f in conv[src].items():
                    key = tuple(sorted(set(chain) | {dst}))
                    a, b2 = buckets[dst].get(key, (0, 0))
                    buckets[dst][key] = (a + mn * f, b2 + mx * f)
                for dst, f in gain[src].items():
                    key = tuple(sorted(set(chain) | {dst}))
                    a, b2 = buckets[dst].get(key, (0, 0))
                    buckets[dst][key] = (a + mn * f, b2 + mx * f)
                buckets[src][chain] = (mn * keep, mx * keep)
        return buckets

    def _type_mult(self, sdb, cfg_base, chain, mode):
        names = ["Damage"] + [c + "Damage" for c in chain]
        if any(c in ELEMENTAL for c in chain):
            names.append("ElementalDamage")
        cfg = dict(cfg_base)
        for c in chain:
            cfg["kw"] = cfg["kw"] | KF(c)
        inc = sdb.sum("INC", cfg, *names)
        more = sdb.more(cfg, *names)
        return (1 + inc / 100) * more, inc, more

    def _enemy_mult(self, dtype, sdb, cfg, is_dot=False):
        edb = self.enemy.moddb
        if dtype == "Physical":
            res = 0
        else:
            names = [dtype + "Resist"] + (["ElementalResist"] if dtype in ELEMENTAL else [])
            ov = edb.override(None, dtype + "Resist")
            if ov is not None:
                res = ov
            else:
                res = edb.sum("BASE", None, *names) * max((1 + edb.sum("INC", None, *names) / 100) * edb.more(None, *names), 0)
            emax = edb.override(None, dtype + "ResistMax")
            if emax is None:
                emax = min(90, 75 + edb.sum("BASE", None, dtype + "ResistMax") + (edb.sum("BASE", None, "ElementalResistMax") if dtype in ELEMENTAL else 0))
            res = max(min(res, emax), -200)
            if sdb.flag(cfg, "Ignore" + dtype + "Resistance") or (dtype in ELEMENTAL and sdb.flag(cfg, "IgnoreElementalResistances")):
                res = 0
        pen = 0
        if not is_dot and dtype != "Physical":
            pen = sdb.sum("BASE", cfg, dtype + "Penetration") + (sdb.sum("BASE", cfg, "ElementalPenetration") if dtype in ELEMENTAL else 0)
        eff_res = max(-200, res - pen) if res - pen < res or pen == 0 else res - pen
        taken_inc = edb.sum("INC", None, "DamageTaken", dtype + "DamageTaken") + (edb.sum("INC", None, "ElementalDamageTaken") if dtype in ELEMENTAL else 0)
        taken_more = edb.more(None, "DamageTaken", dtype + "DamageTaken") * (edb.more(None, "ElementalDamageTaken") if dtype in ELEMENTAL else 1)
        if is_dot:
            taken_inc += edb.sum("INC", None, "DamageTakenOverTime", dtype + "DamageTakenOverTime")
        else:
            taken_inc += edb.sum("INC", None, "DamageTakenWhenHit", dtype + "DamageTakenWhenHit")
        # shock
        shock = 0
        if edb.get_condition("Shocked", None):
            shock = max(edb.sum("BASE", None, "ShockVal"), 15 if not edb.sum("BASE", None, "ShockVal") else 0)
            shock = min(shock * (1 + sdb.sum("INC", cfg, "EnemyShockEffect") / 100), 50)
        taken = (1 + (taken_inc + shock) / 100) * taken_more
        return (1 - eff_res / 100) * taken, eff_res

    def calc_offence_core(self, skill):
        notes = list(self.notes)
        o = self.calc_offence(skill)
        self.notes = notes
        return {"speed": o.get("uses_per_second") or 0, "crit_chance": o.get("crit_chance") or 0,
                "hit_chance": o.get("hit_chance") or 0}

    def calc_offence(self, skill):
        out = {}
        if not skill:
            return {"note": "no main skill"}
        sdb = skill["db"]
        sd = skill["data"]
        fl = skill["flags"]
        out["skill"] = skill["name"]
        out["level"] = skill["level"]
        out["supports"] = skill["supports"]
        is_attack = "attack" in fl
        if "Minion" in skill["types"] or skill["sk"].get("minionList"):
            self.notes.append("Minion damage is not calculated by this engine; export to PoB for minion DPS.")
        if "Triggered" in skill["types"] or skill["sk"].get("isTrigger"):
            self.notes.append("Trigger rates are not modelled; speed shown is the skill's own cast/attack rate.")
        cfg = self._cfg(skill, "hit")
        eff = sd.get("damageEffectiveness") or 1
        hands = []
        if is_attack:
            w1, w2 = self.weapons.get("Weapon 1"), self.weapons.get("Weapon 2")
            if not w1:
                w1 = {"type": "None", "PhysicalMin": 2, "PhysicalMax": 6, "AttackRate": 1.2, "CritChance": 0}
            hands.append(("Weapon 1", w1))
            if w2 and self.player.moddb.conditions.get("DualWielding"):
                hands.append(("Weapon 2", w2))
        else:
            hands.append((None, None))
        results = []
        for hand, w in hands:
            skill["hand"] = hand or "Weapon 1"
            cfg = self._cfg(skill, "hit")
            if hand:
                cfg["skillCond"] = {"MainHandAttack": hand == "Weapon 1", "OffHandAttack": hand == "Weapon 2"}
            base = {}
            for d in DMG_TYPES:
                added_min = sdb.sum("BASE", cfg, d + "Min")
                added_max = sdb.sum("BASE", cfg, d + "Max")
                if is_attack:
                    bm = sd.get("baseMultiplier") or 1
                    wmin, wmax = (w.get(d + "Min") or 0), (w.get(d + "Max") or 0)
                    mn = wmin * bm + added_min * eff
                    mx = wmax * bm + added_max * eff
                else:
                    mn = (sd.get(d + "Min") or 0) + added_min * eff
                    mx = (sd.get(d + "Max") or 0) + added_max * eff
                if mn or mx:
                    base[d] = (mn, mx)
            conv, gain = self._conversion(sdb, cfg)
            buckets = self._flow(base, conv, gain)
            per_type = {}
            total_min = total_max = 0
            ailment_base = {}
            for dst in DMG_TYPES:
                tmin = tmax = 0
                bmin = bmax = 0
                if sdb.flag(cfg, "DealNo" + dst, "DealNoDamage"):
                    continue
                for chain, (mn, mx) in buckets[dst].items():
                    if not (mn or mx):
                        continue
                    m, _, _ = self._type_mult(sdb, cfg, chain, "hit")
                    em, _ = self._enemy_mult(dst, sdb, cfg)
                    if dst == "Physical":
                        avg_hit = (mn + mx) / 2 * m
                        arm = self.enemy_armour * (1 - min(sdb.sum("BASE", cfg, "EnemyArmourReduction"), 100) / 100)
                        dr = arm / (arm + 5 * avg_hit) * 100 if avg_hit > 0 else 0
                        if sdb.flag(cfg, "IgnoreEnemyPhysicalDamageReduction"):
                            dr = 0
                        else:
                            dr = max(0, min(dr, 90) - sdb.sum("BASE", cfg, "EnemyPhysicalDamageReductionOverwhelm", "PhysicalOverwhelm"))
                        em = em * (1 - dr / 100)
                    tmin += mn * m * em
                    tmax += mx * m * em
                    bmin += mn
                    bmax += mx
                if tmin or tmax:
                    per_type[dst] = {"min": rnd(tmin), "max": rnd(tmax)}
                    total_min += tmin
                    total_max += tmax
                    ailment_base[dst] = (bmin, bmax)
            # crit
            if sdb.flag(cfg, "NeverCrit") or self.player.moddb.flag(None, "ResoluteTechnique") or \
                    self.player.moddb.get_condition("HaveResoluteTechnique", None):
                crit = 0.0
            else:
                base_crit = (w.get("CritChance", 0) if is_attack else sd.get("critChance", 0)) or 0
                ov = sdb.override(cfg, "CritChance")
                if ov is not None:
                    crit = ov
                else:
                    edb = self.enemy.moddb
                    crit = ((base_crit + sdb.sum("BASE", cfg, "CritChance") + edb.sum("BASE", None, "SelfCritChance"))
                            * (1 + (sdb.sum("INC", cfg, "CritChance") + edb.sum("INC", None, "SelfCritChance")) / 100)
                            * sdb.more(cfg, "CritChance") * edb.more(None, "SelfCritChance"))
                crit = min(max(crit, 0), 100)
                if sdb.flag(cfg, "SpellSkillsAlwaysCrit") and "spell" in fl:
                    crit = 100
            crit_multi = 100 + sdb.sum("BASE", cfg, "CritMultiplier")
            if self.enemy.moddb.sum("BASE", None, "SelfCritMultiplier"):
                crit_multi += self.enemy.moddb.sum("BASE", None, "SelfCritMultiplier")
            crit_multi *= sdb.more(cfg, "CritMultiplier") if sdb.more(cfg, "CritMultiplier") != 1 else 1
            if sdb.flag(cfg, "NoCritMultiplier"):
                crit_multi = 100
            # hit chance
            if is_attack and not sdb.flag(cfg, "CannotBeEvaded") and not self.player.moddb.get_condition("HaveResoluteTechnique", None):
                acc = sdb.sum("BASE", cfg, "Accuracy") * (1 + sdb.sum("INC", cfg, "Accuracy") / 100) * sdb.more(cfg, "Accuracy")
                ev = self.enemy_evasion
                hit_chance = max(5, min(100, 125 * acc / (acc + (ev * 0.2) ** 0.9))) if acc > 0 else 5
            else:
                hit_chance = 100
            # speed
            if is_attack:
                speed = w.get("AttackRate", 1) * (1 + (sd.get("attackSpeedMultiplier") or 0) / 100)
                sp_cfg = cfg
            else:
                ct = skill["sk"].get("castTime") or 1
                speed = 1 / ct if ct else 1
                sp_cfg = dict(cfg, flags=cfg["flags"] | MF("Cast"))
            speed *= (1 + sdb.sum("INC", sp_cfg, "Speed") / 100) * sdb.more(sp_cfg, "Speed")
            speed *= self.player.output.get("ActionSpeedMod", 1)
            if sd.get("cooldown"):
                cd = sd["cooldown"] / (1 + sdb.sum("INC", cfg, "CooldownRecovery") / 100)
                speed = min(speed, (sd.get("storedUses") or 1) / cd if cd else speed)
                out["cooldown"] = rnd(cd, 3)
            avg_nc = (total_min + total_max) / 2
            avg_crit = avg_nc * crit_multi / 100
            avg = avg_nc * (1 - crit / 100) + avg_crit * crit / 100
            dps_mult = sdb.sum("BASE", cfg, "DPS") or 1
            if sd.get("dpsMultiplier"):
                dps_mult *= sd["dpsMultiplier"]
            dps = avg * speed * hit_chance / 100 * dps_mult
            results.append({"hand": hand, "per_type": per_type, "average_hit": avg, "crit_chance": crit,
                            "crit_multiplier": crit_multi, "hit_chance": hit_chance, "speed": speed, "dps": dps,
                            "ailment_base": ailment_base, "cfg": cfg, "avg_nc": avg_nc})
        # combine hands (alternating attacks)
        if len(results) == 2:
            r1, r2 = results
            speed = 2 / (1 / r1["speed"] + 1 / r2["speed"])
            comb = {
                "average_hit": (r1["average_hit"] + r2["average_hit"]) / 2,
                "crit_chance": (r1["crit_chance"] + r2["crit_chance"]) / 2,
                "hit_chance": (r1["hit_chance"] + r2["hit_chance"]) / 2,
                "speed": speed,
            }
            comb["dps"] = (r1["average_hit"] * r1["hit_chance"] + r2["average_hit"] * r2["hit_chance"]) / 2 / 100 * speed
            main = r1
        else:
            main = results[0]
            comb = {k: main[k] for k in ("average_hit", "crit_chance", "hit_chance", "speed", "dps")}
        if skill.get("trigger"):
            tr = self.trigger_rate(skill, sdb, cfg)
            out["trigger"] = tr
            if tr.get("rate") is not None:
                comb["speed"] = tr["rate"]
                comb["dps"] = comb["average_hit"] * tr["rate"] * comb["hit_chance"] / 100
            else:
                comb["dps"] = 0
        hit_dps = comb["dps"] if "hit" in fl else 0
        out.update({
            "hit_dps": rnd(hit_dps, 1),
            "average_hit": rnd(comb["average_hit"], 1) if "hit" in fl else 0,
            "damage_per_type": main["per_type"] if "hit" in fl else {},
            "crit_chance": rnd(comb["crit_chance"], 2),
            "crit_multiplier": rnd(main["crit_multiplier"], 1),
            "hit_chance": rnd(comb["hit_chance"], 1),
            "uses_per_second": rnd(comb["speed"], 3),
            "cast_or_attack_time": rnd(1 / comb["speed"], 3) if comb["speed"] else None,
        })
        # skill-specific base damage PoB computes in code (preDamageFunc)
        if sd.get("RFLifeMultiplier") or sd.get("RFESMultiplier"):
            o = self.player.output
            sd["FireDot"] = (o.get("Life", 0) * (sd.get("RFLifeMultiplier") or 0) +
                             o.get("EnergyShield", 0) * (sd.get("RFESMultiplier") or 0))
        elif skill["sk"].get("customCalc"):
            self.notes.append(f"{skill['name']} has special-case maths in PoB that is only partly modelled here.")
        # skill DoT
        dot_cfg = self._cfg(skill, "dot")
        dot_total = 0
        dot_parts = {}
        for d in DMG_TYPES:
            base_dot = (sd.get(d + "Dot") or 0) + sdb.sum("BASE", dot_cfg, d + "Dot")
            if not base_dot:
                continue
            m, _, _ = self._type_mult(sdb, dict(dot_cfg), (d,), "dot")
            dm = 1 + (sdb.sum("BASE", dot_cfg, "DotMultiplier", d + "DotMultiplier") + (sdb.sum("BASE", dot_cfg, "ElementalDotMultiplier") if d in ELEMENTAL else 0)) / 100
            em, _ = self._enemy_mult(d, sdb, dot_cfg, is_dot=True)
            v = base_dot * m * dm * em
            dot_parts[d] = rnd(v, 1)
            dot_total += v
        if dot_total:
            out["dot_dps"] = rnd(dot_total, 1)
            out["dot_per_type"] = dot_parts
        # ailments
        crit = comb["crit_chance"] / 100
        hits_ps = comb["speed"] * comb["hit_chance"] / 100 if "hit" in fl else 0
        ab = main["ailment_base"]

        def ailment(mode, dtypes, chance_name, pct, dur_base, stacks_mode):
            if not hits_ps:
                return None
            acfg = self._cfg(skill, mode)
            if sdb.flag(main["cfg"], {"ignite": "CannotIgnite", "bleed": "CannotBleed", "poison": "CannotPoison"}[mode]):
                return None
            chance = min(100, sdb.sum("BASE", main["cfg"], chance_name))
            if sdb.flag(main["cfg"], {"ignite": "IgniteChanceIsOneHundred", "bleed": "BleedChanceIsOneHundred",
                                      "poison": "PoisonChanceIsOneHundred"}[mode]):
                chance = 100
            if chance <= 0:
                return None
            base = sum((ab.get(d, (0, 0))[0] + ab.get(d, (0, 0))[1]) / 2 for d in dtypes)
            if not base:
                return None
            # crit ailments: +50% dot multiplier from base (Condition CriticalStrike)
            crit_cfg = dict(acfg, skillCond={"CriticalStrike": True})
            tot = 0
            for is_crit, w8 in ((False, 1 - crit), (True, crit)):
                c = crit_cfg if is_crit else acfg
                inc_more = 1
                v = 0
                for d in dtypes:
                    dbase = (ab.get(d, (0, 0))[0] + ab.get(d, (0, 0))[1]) / 2
                    if not dbase:
                        continue
                    m, _, _ = self._type_mult(sdb, dict(c), (d,), mode)
                    dm = 1 + (sdb.sum("BASE", c, "DotMultiplier", d + "DotMultiplier") +
                              (sdb.sum("BASE", c, "ElementalDotMultiplier") if d in ELEMENTAL else 0) +
                              sdb.sum("BASE", c, {"ignite": "IgniteDotMultiplier", "bleed": "BleedDotMultiplier",
                                                  "poison": "PoisonDotMultiplier"}[mode])) / 100
                    em, _ = self._enemy_mult({"bleed": "Physical", "poison": "Chaos", "ignite": "Fire"}[mode], sdb, c, is_dot=True)
                    v += dbase * pct * m * dm * em * inc_more
                tot += v * w8
            dur = dur_base * (1 + sdb.sum("INC", acfg, "EnemyIgniteDuration" if mode == "ignite" else
                                         "EnemyBleedDuration" if mode == "bleed" else "EnemyPoisonDuration",
                                         "EnemyAilmentDuration") / 100)
            res = {"chance": chance, "dps_per_stack": rnd(tot, 1), "duration": rnd(dur, 2)}
            if stacks_mode == "stack":
                stacks = hits_ps * chance / 100 * dur
                res["stacks"] = rnd(stacks, 2)
                res["dps"] = rnd(tot * stacks, 1)
            else:
                res["dps"] = rnd(tot, 1) if chance >= 100 or hits_ps * chance / 100 * dur >= 1 else rnd(tot * min(1, hits_ps * chance / 100 * dur), 1)
            return res
        ign = ailment("ignite", ["Fire"] + (["Physical", "Lightning", "Cold", "Chaos"] if sdb.flag(main["cfg"], "AllDamageCanIgnite") else []),
                      "EnemyIgniteChance", 0.9, 4, "single")
        if ign:
            out["ignite"] = ign
        if is_attack or sdb.flag(main["cfg"], "SpellsCanBleed"):
            bl = ailment("bleed", ["Physical"], "BleedChance", 0.7, 5, "single")
            if bl:
                if self.enemy.moddb.get_condition("Moving", None):
                    bl["dps"] = rnd(bl["dps"] * 3, 1)
                out["bleed"] = bl
        po = ailment("poison", ["Physical", "Chaos"] + (["Fire", "Cold", "Lightning"] if sdb.flag(main["cfg"], "AllDamageCanPoison") else []),
                     "PoisonChance", 0.3, 2, "stack")
        if po:
            out["poison"] = po
        total = hit_dps + out.get("dot_dps", 0) + sum(out.get(k, {}).get("dps", 0) for k in ("ignite", "bleed", "poison"))
        out["total_dps"] = rnd(total, 1)
        if not total:
            out["note"] = "skill deals no damage by itself (buff/aura/utility/minion)"
        # cost
        cost = (skill["row"].get("cost") or {})
        if cost:
            mult = skill["manaMult"] / 100
            inc = sdb.sum("INC", cfg, "Cost", "ManaCost")
            more = sdb.more(cfg, "Cost", "ManaCost")
            out["cost"] = {k: rnd(v * mult * (1 + inc / 100) * more, 1) for k, v in cost.items()}
        out["enemy"] = {
            "boss": self.bd["boss"], "level": self.bd["level"],
            "resist": {d: rnd(self._enemy_mult(d, sdb, cfg)[1]) for d in ("Fire", "Cold", "Lightning", "Chaos")},
            "curses": getattr(self, "curses", []),
        }
        return out

    # -------------------------------------------------------------- run
    def run(self):
        self.setup()
        b = self.build
        main = None
        mg = b.main_group if b.skill_groups else None
        if mg and 1 <= mg <= len(b.skill_groups):
            grp = b.skill_groups[mg - 1]
            idx = next((i for i, g in enumerate(grp.get("gems", []))
                        if g.get("enabled", True) and data.find_gem(g["name"]) and not data.find_gem(g["name"])["support"]), None)
            if idx is not None:
                main = (grp, idx)
        if main is None:
            # like PoB: with no active skill the main skill is the Default Attack
            main = ({"slot": None, "enabled": True, "gems": [{"name": "Default Attack", "level": 1, "quality": 0,
                     "enabled": True, "_gd": {"name": "Default Attack", "skillId": "Melee", "support": False,
                                              "tagList": ["attack", "melee"], "req": {}}}]}, 0)
        # first pass: defence (attributes needed by PerStat in skills)
        self.reserved = {"Mana": 0, "Life": 0, "ManaPercent": 0.0, "LifePercent": 0.0}
        self.calc_defence()
        self.apply_buffs(None)
        # recompute defence with buffs applied
        self.player.output = {}
        db = self.player.moddb
        self.calc_defence()
        skill = self.build_skill(main[0], main[1]) if main else None
        off = self.calc_offence(skill) if skill else {"note": "no main skill selected"}
        if skill and minion_types(skill):
            try:
                mc = MinionCalculator(self, skill, main[0])
                off["minion"] = mc.result()
                if not off.get("total_dps"):
                    off["total_dps"] = off["minion"]["total_dps"]
                    off.pop("note", None)
                self.notes = [n for n in self.notes if not n.startswith("Minion damage")]
            except Exception as ex:  # keep player results even if a minion type is unusual
                self.notes.append(f"Minion calc failed: {type(ex).__name__}: {ex}")
        return self.player.output, off


@functools.lru_cache(maxsize=None)
def _skill_ids():
    return {id(v): k for k, v in skills_data().items()}


def skills_id(sk):
    return _skill_ids().get(id(sk))


# ======================================================================== minions
def minion_types(skill):
    lst = skill["sk"].get("minionList") or []
    if isinstance(lst, dict):
        lst = list(lst.values())
    return [m for m in lst if isinstance(m, str)]


def minion_data(mtype):
    m = data.misc()
    return m["minions"].get(mtype) or m["spectres"].get(mtype)


class MinionCalculator(Calculator):
    """Runs the offence/defence code for a minion actor (PoB: env.minion)."""

    def __init__(self, parent, summon, group):
        self.parent = parent
        self.build = parent.build
        self.summon = summon
        self.group = group
        self.notes = []
        self.unparsed = []
        cfg = parent.cfg_in
        types = minion_types(summon)
        want = cfg.get("skillMinion") or group.get("minion")
        self.mtype = want if want in types else types[0]
        self.md = minion_data(self.mtype) or {}

    def setup(self):
        p = self.parent
        md = self.md
        self.cfg_in, self.bd = p.cfg_in, p.bd
        self.enemy_armour, self.enemy_evasion = p.enemy_armour, p.enemy_evasion
        self.enemy = p.enemy
        self.player = Actor("minion")
        self.player.other = p.enemy
        db = self.player.moddb
        db.conditions.update({"Combat": True, "Effective": True})
        lvl = self.summon["stats"].get("base_display_minion_actor_level") or self.summon["row"].get("levelRequirement") or 1
        lvl = int(min(max(lvl, 1), 100))
        self.level = lvl
        db.multipliers["Level"] = lvl
        c = consts()
        is_spectre = self.mtype in data.misc()["spectres"]
        life_tab = (c["monsterLifeTable2"] if md.get("lifeScaling") == "AltLife1" else
                    c["monsterLifeTable3"] if md.get("lifeScaling") == "AltLife2" else
                    c["monsterLifeTable"] if is_spectre else c["monsterAllyLifeTable"])
        db.new("Life", "BASE", math.floor(life_tab[lvl - 1] * md.get("life", 1)))
        if md.get("energyShield"):
            db.new("EnergyShield", "BASE", math.floor(c["monsterAllyLifeTable"][lvl - 1] * md.get("life", 1) * md["energyShield"]))
        db.new("Armour", "BASE", rnd(c["monsterArmourTable"][lvl - 1] * (md.get("armour") or 1)))
        db.new("Evasion", "BASE", rnd(c["monsterEvasionTable"][lvl - 1] * (md.get("evasion") or 1)))
        db.new("CannotBeEvaded", "FLAG", True, "Minion attacks always hit")
        mc = c.get("monsterConstants", {})
        db.new("CritMultiplier", "BASE", mc.get("base_critical_strike_multiplier", 130) - 100)
        db.new("DotMultiplier", "BASE", mc.get("critical_ailment_dot_multiplier_+", 50), "Base", 0, 0, {"type": "Condition", "var": "CriticalStrike"})
        for r in ("Fire", "Cold", "Lightning", "Chaos"):
            db.new(r + "Resist", "BASE", md.get(r.lower() + "Resist", 0))
            db.new(r + "ResistMax", "BASE", 75)
        db.new("ProjectileCount", "BASE", 1)
        for m in md.get("modList") or []:
            if m.get("k") == "mod":
                db.add(norm_mod(m, "Minion base"))
        # player's "Minions have/deal ..." modifiers (+ those from the summon gem and its supports)
        for v in self.summon["db"].list(p._cfg(self.summon, "buff"), "MinionModifier"):
            if isinstance(v, dict) and isinstance(v.get("mod"), dict):
                db.add(norm_mod(v["mod"], "Player:MinionModifier"))
        for m in getattr(p, "ally_buffs", []):
            db.add(dict(m))
        attack_time = md.get("attackTime", 1)
        tab = c["monsterDamageTable"] if (is_spectre or md.get("hostile")) else c["monsterAllyDamageTable"]
        dmg = tab[lvl - 1] * md.get("damage", 1)
        if not md.get("baseDamageIgnoresAttackSpeed"):
            dmg *= attack_time
        spread = md.get("damageSpread", 0.2)
        self.weapons = {"Weapon 1": {"type": md.get("weaponType1", "None"), "AttackRate": 1 / attack_time, "CritChance": 5,
                                     "PhysicalMin": rnd(dmg * (1 - spread)), "PhysicalMax": rnd(dmg * (1 + spread)),
                                     "range": md.get("attackRange")}}
        self.item_list = {}
        self.reserved = {"ManaPercent": 0, "Mana": 0}

    def pick_skill(self):
        skills = [s for s in (self.md.get("skillList") or []) if s in skills_data()]
        idx = int(self.cfg_in.get("skillMinionSkill") or self.group.get("minionSkill") or 1)
        chosen = skills[min(max(idx, 1), len(skills)) - 1] if skills else None
        return chosen, skills

    def result(self):
        self.setup()
        self.calc_defence()
        sid, options = self.pick_skill()
        if not sid:
            return {"type": self.mtype, "note": "no usable minion skill"}
        sk = skills_data()[sid]
        pseudo = {"name": sk["name"], "skillId": sid, "support": False, "req": {},
                  "tagList": [t.lower() for t in sk.get("skillTypes", [])]}
        gems = [{"name": sk["name"], "level": 1, "quality": 0, "enabled": True, "_gd": pseudo}]
        gems += [g for g in self.group.get("gems", []) if g.get("enabled", True)
                 and (data.find_gem(g["name"]) or {}).get("support")]
        skill = self.build_skill({"slot": self.group.get("slot"), "gems": gems}, 0)
        off = self.calc_offence(skill)
        o = self.player.output
        count = 1
        lim = self.md.get("limit")
        if lim:
            count = max(1, int(self.summon["db"].sum("BASE", self.parent._cfg(self.summon, "buff"), lim)))
        elif "base_number_of_zombies_allowed" in self.summon["stats"]:
            count = int(self.summon["stats"]["base_number_of_zombies_allowed"])
        per = off.get("total_dps", 0) or 0
        return {"type": self.mtype, "name": self.md.get("name", self.mtype), "level": self.level,
                "skill": off.get("skill"), "skill_options": [skills_data()[x]["name"] for x in options],
                "supports_applied": off.get("supports"), "count": count,
                "dps_per_minion": per, "total_dps": rnd(per * count, 1),
                "average_hit": off.get("average_hit"), "uses_per_second": off.get("uses_per_second"),
                "crit_chance": off.get("crit_chance"), "life": o.get("Life"), "energy_shield": o.get("EnergyShield"),
                "resistances": {r: o.get(r + "Resist") for r in ("Fire", "Cold", "Lightning", "Chaos")},
                "ignite": off.get("ignite"), "bleed": off.get("bleed"), "poison": off.get("poison"),
                "notes": self.notes}


# ======================================================================== public
def calculate(build) -> dict:
    """Full calculation. Returns a JSON-able dict."""
    c = Calculator(build)
    outp, off = c.run()
    o = outp
    res = {
        "engine": "poe_build calc (PoB-data based; minions/triggers/cluster trees not modelled)",
        "offence": off,
        "defence": {
            "life": o.get("Life"), "mana": o.get("Mana"), "mana_unreserved": o.get("ManaUnreserved"),
            "mana_reserved_percent": o.get("ManaReservedPercent"),
            "energy_shield": o.get("EnergyShield"), "ward": o.get("Ward"), "armour": o.get("Armour"),
            "evasion": o.get("Evasion"),
            "block": rnd(o.get("BlockChance", 0), 1), "spell_block": rnd(o.get("SpellBlockChance", 0), 1),
            "spell_suppression": rnd(o.get("SpellSuppressionChance", 0), 1),
            "resistances": {el: {"total": o.get(el + "ResistTotal"), "max": o.get(el + "ResistMax"),
                                 "effective": o.get(el + "Resist"), "overcap": o.get(el + "ResistOverCap")}
                            for el in ("Fire", "Cold", "Lightning", "Chaos")},
            "life_regen": o.get("LifeRegen"), "mana_regen": o.get("ManaRegen"),
            "es_recharge": o.get("EnergyShieldRecharge"), "movement_speed": o.get("MovementSpeedMod"),
            "accuracy": o.get("Accuracy"),
            "max_hit_taken": {k: o.get(k + "MaximumHitTaken") for k in ("Physical", "Fire", "Cold", "Lightning", "Chaos")},
            "charges": {k: o.get(k + "Charges") for k in ("Power", "Frenzy", "Endurance")},
        },
        "attributes": {"str": o.get("Str"), "dex": o.get("Dex"), "int": o.get("Int")},
        "buffs": getattr(c, "buffs", []),
        "notes": c.notes,
        "unparsed_mods": [f"{a}:{b}: {t}" for a, b, t in c.unparsed][:60],
        "unparsed_count": len(c.unparsed),
        "cluster_points": getattr(c, "cluster_points", 0),
    }
    return res

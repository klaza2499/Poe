"""Mod text -> structured modifiers, using Path of Building's own parse results.

PoB ships ModCache.lua: ~19k mod lines already parsed by its ModParser. We look a
line up exactly; otherwise we look up a *template* (numbers replaced by #) and
substitute the new numbers into the cached result. Lines PoB never parsed are
returned as unparsed so the caller can report them.
"""
from __future__ import annotations

import copy
import functools
import re

from . import data

_NUM = re.compile(r"(?:(?<=^)|(?<=[\s(]))[+-]?\d+(?:\.\d+)?|\d+(?:\.\d+)?")
_RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")
_TAG_NUM_FIELDS = ("div", "limit", "threshold", "base", "percent", "globalLimit")


def roll(line: str, at: float = 0.5) -> str:
    def f(m):
        a, b = float(m.group(1)), float(m.group(2))
        v = a + (b - a) * at
        return str(int(round(v))) if a.is_integer() and b.is_integer() else f"{v:g}"
    return _RANGE.sub(f, line)


def _tmpl(line):
    return _NUM.sub("#", line), [float(x) for x in _NUM.findall(line)]


@functools.lru_cache(maxsize=None)
def cache():
    return data.load("modcache")


@functools.lru_cache(maxsize=None)
def lower_cache():
    return {k.lower(): v for k, v in cache().items()}


@functools.lru_cache(maxsize=None)
def templates():
    idx = {}
    for line, mods in cache().items():
        t, nums = _tmpl(line.lower())
        if nums:
            idx.setdefault(t, []).append((nums, mods))
    return idx


_PHRASE = re.compile(r"^(\+|-)?#(%)? (increased|reduced|more|less|to|additional) (.+)$")


@functools.lru_cache(maxsize=None)
def phrases():
    """Derive phrase -> (modName, type, flags, kw, tags) from simple cached lines, e.g.
    '10% increased Fire Damage' -> ('fire damage', INC) => FireDamage."""
    out = {}
    for line, mods in cache().items():
        if len(mods) != 1:
            continue
        m = mods[0]
        t, nums = _tmpl(line.lower())
        mm = _PHRASE.match(t)
        if not mm or len(nums) != 1 or not isinstance(m.get("value"), (int, float)) or isinstance(m.get("value"), bool):
            continue
        word, phrase = mm.group(3), mm.group(4)
        kind = {"increased": "INC", "reduced": "INC", "more": "MORE", "less": "MORE"}.get(word, "BASE")
        if kind != m.get("type"):
            continue
        key = ("pct" if mm.group(2) else "flat") if kind == "BASE" else kind
        sign = (-1 if word in ("reduced", "less") else 1) * (-1 if nums[0] < 0 else 1)
        if abs(abs(m["value"]) - abs(nums[0])) > 1e-9 or (m["value"] < 0) != (sign < 0):
            continue
        out.setdefault((key, phrase), {k: m[k] for k in ("name", "type", "flags", "kw", "tags") if k in m})
    return out


_NEARBY = re.compile(r"^nearby enemies (deal|have) (.+)$", re.I)
_GEMPROP = re.compile(r"^([+-]?\d+)%? to (level|quality) of (all|socketed) ?(.*?) ?(skill |support )?gems$", re.I)


_TYPES = ("Physical", "Lightning", "Cold", "Fire", "Chaos")


def _deal_no(line):
    """'Deal no Non-Fire Damage', 'Deal no Physical Damage' (PoB DealNo<Type> flags)."""
    m = re.match(r"^deal no (non-)?(physical|lightning|cold|fire|chaos|elemental) damage$", line.strip(), re.I)
    if not m:
        return None
    word = m.group(2).capitalize()
    group = ["Lightning", "Cold", "Fire"] if word == "Elemental" else [word]
    types = [t for t in _TYPES if (t not in group) == bool(m.group(1))]
    return [{"name": "DealNo" + t, "type": "FLAG", "value": True, "flags": 0, "kw": 0} for t in types]


def _gem_property(line):
    m = _GEMPROP.match(line.strip())
    if not m:
        return None
    v, key, scope, words, kind = int(m.group(1)), m.group(2).lower(), m.group(3).lower(), m.group(4).lower(), (m.group(5) or "").strip().lower()
    kws = [w for w in words.split() if w]
    if kind == "skill":
        kws.append("grants_active_skill" if not kws else "skill")
    elif kind == "support":
        kws.append("support")
    val = {"key": key, "value": v}
    if len(kws) > 1:
        val["keywordList"] = kws
    else:
        val["keyword"] = kws[0] if kws else "all"
    mod = {"name": "GemProperty", "type": "LIST", "value": val, "flags": 0, "kw": 0}
    if scope == "socketed":
        mod["tags"] = [{"type": "SocketedIn", "slotName": "{SlotName}"}]
    return [mod]


def _phrase_parse(line):
    nm = _NEARBY.match(line.strip())
    if nm:
        rest = nm.group(2)
        if nm.group(1).lower() == "deal":
            rest = re.sub(r"^(\d+(?:\.\d+)?%) (less|more|increased|reduced) (.+)$", r"\1 \2 \3", rest)
        inner = _parse_cached(rest)
        if inner and len(inner) == 1 and inner[0].get("type") in ("BASE", "INC", "MORE", "FLAG"):
            m = inner[0]
            return [{"name": "EnemyModifier", "type": "LIST", "flags": 0, "kw": 0,
                     "value": {"mod": {"name": m["name"], "type": m["type"], "value": m["value"],
                                       "flags": m.get("flags", 0), "keywordFlags": m.get("kw", 0)}}}]
        return None
    t, nums = _tmpl(line.lower())
    mm = _PHRASE.match(t)
    if not mm or len(nums) != 1:
        return None
    word, phrase = mm.group(3), mm.group(4)
    v = nums[0]
    v = int(v) if float(v).is_integer() else v
    if word in ("increased", "reduced", "more", "less"):
        kind = "INC" if word in ("increased", "reduced") else "MORE"
        base = phrases().get((kind, phrase)) or phrases().get(("INC" if kind == "MORE" else "MORE", phrase))
        if not base:
            return None
        sign = -1 if word in ("reduced", "less") else 1
        return [dict(copy.deepcopy(base), type=kind, value=sign * abs(v))]
    for key in (("pct", "flat") if mm.group(2) else ("flat", "pct")):
        base = phrases().get((key, phrase))
        if base:
            return [dict(copy.deepcopy(base), value=v)]
    return None


def _subst(mods, old, new):
    """Map each numeric value in mods that equals an old number onto the new one."""
    ok = True

    def mapv(v, field=None):
        nonlocal ok
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        for i, o in enumerate(old):
            if o == 0:
                continue
            # value sign relative to the number in the text (e.g. "reduced" stores negatives)
            rel = (1 if v >= 0 else -1) * (1 if o >= 0 else -1)
            if abs(abs(v) - abs(o)) < 1e-9:
                nv = abs(new[i]) * rel * (1 if new[i] >= 0 else -1)
                return int(nv) if float(nv).is_integer() else nv
            # values stored as fractions (e.g. 1% -> 0.01) or per-second rates
            for scale in (100, 60, 1000):
                if abs(abs(v) * scale - abs(o)) < 1e-6:
                    return abs(new[i]) / scale * rel * (1 if new[i] >= 0 else -1)
        if field == "value" and v not in (0, 1, -1, 100):
            ok = False
        return v

    out = copy.deepcopy(mods)
    for m in out:
        if isinstance(m.get("value"), dict):
            for k, x in list(m["value"].items()):
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    m["value"][k] = mapv(x, "value" if k == "value" else None)
            if isinstance(m["value"].get("mod"), dict):
                m["value"]["mod"]["value"] = mapv(m["value"]["mod"].get("value"), "value")
        else:
            m["value"] = mapv(m.get("value"), "value")
        for t in m.get("tags", []):
            for f in _TAG_NUM_FIELDS:
                if f in t:
                    t[f] = mapv(t[f])
    return out if ok else None


@functools.lru_cache(maxsize=50000)
def _parse_cached(line):
    c = cache()
    if line in c:
        return copy.deepcopy(c[line])
    lc = lower_cache().get(line.lower())
    if lc is not None:
        return copy.deepcopy(lc)
    r = _template_parse(line)
    if r is None:
        r = _deal_no(line)
    if r is None:
        r = _gem_property(line)
    if r is None:
        r = _phrase_parse(line)
    if r is None:
        # users often omit the % sign ("+20 to all resistances")
        alt = re.sub(r"^([+-]?\d+(?:\.\d+)?) to ", r"\1% to ", line)
        if alt != line:
            r = _template_parse(alt) or _phrase_parse(alt)
    return r


def _template_parse(line):
    t, nums = _tmpl(line.lower())
    cands = templates().get(t)
    if not cands:
        return None
    # prefer candidate whose numbers differ least in count of distinct values
    for old, mods in sorted(cands, key=lambda x: len(set(x[0])) != len(set(nums))):
        if len(old) != len(nums):
            continue
        # ambiguous if the cached line repeats a number but the new line doesn't (or vice versa)
        if len(set(old)) != len(set(nums)) and len(set(old)) < len(old):
            continue
        r = _subst(mods, old, nums)
        if r is not None:
            return r
    return None


def parse_line(line: str):
    """Return list of mod dicts, or None if PoB can't parse the line."""
    line = line.strip()
    if not line:
        return []
    r = _parse_cached(line)
    return copy.deepcopy(r) if r is not None else None

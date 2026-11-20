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

_NUM = re.compile(r"\d+(?:\.\d+)?")
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
def templates():
    idx = {}
    for line, mods in cache().items():
        t, nums = _tmpl(line)
        if nums:
            idx.setdefault(t, []).append((nums, mods))
    return idx


def _subst(mods, old, new):
    """Map each numeric value in mods that equals an old number onto the new one."""
    ok = True

    def mapv(v, field=None):
        nonlocal ok
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        for i, o in enumerate(old):
            if abs(abs(v) - o) < 1e-9 and o != 0:
                nv = new[i] if v >= 0 else -new[i]
                return int(nv) if float(nv).is_integer() else nv
            # values stored as fractions (e.g. 1% -> 0.01) or per-second rates
            for scale in (100, 60, 1000):
                if abs(abs(v) * scale - o) < 1e-6 and o != 0:
                    nv = (new[i] / scale) * (1 if v >= 0 else -1)
                    return nv
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
    t, nums = _tmpl(line)
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

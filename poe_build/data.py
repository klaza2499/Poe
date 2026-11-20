"""Game data access (lazy-loaded from poe_build/data/*.json.gz)."""
from __future__ import annotations

import functools
import gzip
import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


@functools.lru_cache(maxsize=None)
def load(name: str):
    with open(os.path.join(DATA_DIR, name + ".json.gz"), "rb") as f:
        return json.loads(gzip.decompress(f.read()))


@functools.lru_cache(maxsize=None)
def meta():
    with open(os.path.join(DATA_DIR, "meta.json"), encoding="utf-8") as f:
        return json.load(f)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def tree():
    return load("tree")


@functools.lru_cache(maxsize=None)
def nodes():
    return {int(k): v for k, v in tree()["nodes"].items()}


def gems():
    return load("gems")


@functools.lru_cache(maxsize=None)
def gem_index():
    idx = {}
    for gid, g in gems().items():
        for key in (g["name"], g.get("skillId") or "", g.get("variantId") or ""):
            k = norm(key)
            if k and k not in idx:
                idx[k] = g
        # "Spell Echo Support" style names
        if g["support"]:
            idx.setdefault(norm(g["name"] + " support"), g)
    return idx


def find_gem(name: str):
    g = gem_index().get(norm(name))
    if g:
        return g
    k = norm(re.sub(r"\bsupport\b", "", name, flags=re.I))
    return gem_index().get(k)


def uniques():
    return load("uniques")


@functools.lru_cache(maxsize=None)
def unique_index():
    return {norm(u["name"]): u for u in uniques()}


def find_unique(name: str):
    return unique_index().get(norm(name))


def bases():
    return load("bases")


@functools.lru_cache(maxsize=None)
def base_index():
    return {norm(k): (k, v) for k, v in bases().items()}


def find_base(name: str):
    return base_index().get(norm(name))


def mods():
    return load("mods")


def misc():
    return load("misc")


def classes():
    return tree()["classes"]


def find_class(name: str):
    """Return (class, ascendancy|None) for a class or ascendancy name."""
    n = norm(name)
    for c in classes():
        if norm(c["name"]) == n:
            return c, None
        for a in c["ascendancies"]:
            if norm(a["id"]) == n or norm(a["name"]) == n:
                return c, a
    return None, None


def search(items, query, fields, limit=25):
    """Generic substring search: every word of query must appear in one of fields."""
    words = norm(query).split()
    out = []
    for it in items:
        hay = norm(" ".join(_flat(it.get(f)) for f in fields))
        if all(w in hay for w in words):
            out.append(it)
            if len(out) >= limit:
                break
    return out


def _flat(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_flat(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_flat(x) for x in v.values())
    return str(v)

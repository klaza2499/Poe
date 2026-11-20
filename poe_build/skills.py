"""Skill gems & socket groups, including support-gem compatibility checks."""
from __future__ import annotations

from . import data

OPS = {"AND", "OR", "NOT"}


def _eval_types(expr, types):
    """PoB stores require/exclude skill types in postfix with AND/OR/NOT tokens."""
    if not expr:
        return None
    if not any(t in OPS for t in expr):
        return any(t in types for t in expr)
    st = []
    for t in expr:
        if t == "AND":
            b, a = st.pop(), st.pop(); st.append(a and b)
        elif t == "OR":
            b, a = st.pop(), st.pop(); st.append(a or b)
        elif t == "NOT":
            st.append(not st.pop())
        else:
            st.append(t in types)
    return any(st)


def support_fits(support: dict, active: dict) -> tuple[bool, str]:
    types = set(active.get("skillTypes", []))
    req = support.get("requireSkillTypes")
    if req and not _eval_types(req, types):
        return False, f"{support['name']} needs skill types {req}"
    exc = support.get("excludeSkillTypes")
    if exc and _eval_types(exc, types):
        return False, f"{support['name']} cannot support {active['name']} (excluded types {exc})"
    return True, ""


def resolve_gem(name):
    g = data.find_gem(name)
    if not g:
        raise KeyError(f"gem '{name}' not found (try search_gems)")
    return g


def check_group(group: dict) -> list[str]:
    """Return warnings for a socket group {slot, gems:[{name,level,quality}]}."""
    warns = []
    gems = []
    for g in group.get("gems", []):
        try:
            gems.append((g, resolve_gem(g["name"])))
        except KeyError as e:
            warns.append(str(e))
    actives = [gd for g, gd in gems if not gd["support"]]
    supports = [gd for g, gd in gems if gd["support"]]
    for s in supports:
        if actives and not any(support_fits(s, a)[0] for a in actives):
            warns.append(support_fits(s, actives[0])[1])
    if len(group.get("gems", [])) > 6 and group.get("slot") not in (None, ""):
        warns.append(f"{len(group['gems'])} gems in {group.get('slot')} (max 6 sockets)")
    return warns


def compatible_supports(active_name: str, query: str | None = None, limit=None):
    a = resolve_gem(active_name)
    out = []
    for g in data.gems().values():
        if not g["support"]:
            continue
        if query and not all(w in data.norm(g["name"] + " " + g.get("description", "")) for w in data.norm(query).split()):
            continue
        ok, _ = support_fits(g, a)
        if ok:
            out.append(g["name"])
    return sorted(set(out))[:limit]

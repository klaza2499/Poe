"""Turn PoB modifier expressions into data — without executing Lua.

PoB skill data contains source like
    mod("Damage", "MORE", nil, ModFlag.Spell, bit.bor(KeywordFlag.Hit, KeywordFlag.Ailment), { type = "GlobalEffect", effectType = "Aura" })
    skill("radius", 25)
    flag("Condition:Foo")
This module tokenizes such text (lualit) and interprets only a fixed whitelist:
mod/skill/flag constructors, ModFlag/KeywordFlag/SkillType constants, bit.bor/band,
arithmetic and table literals. Anything else becomes {"k": "unknown"}.
"""
from __future__ import annotations

import re

from lualit import Parser, tokenize  # noqa: F401

MODFLAG: dict[str, int] = {}
KEYWORDFLAG: dict[str, int] = {}


def load_flags(global_lua: str):
    for m in re.finditer(r"^(ModFlag|KeywordFlag)\.(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", global_lua, re.M):
        (MODFLAG if m.group(1) == "ModFlag" else KEYWORDFLAG)[m.group(2)] = int(m.group(3), 0)
    st = {}
    m = re.search(r"^SkillType = \{(.*?)^\}", global_lua, re.M | re.S)
    if m:
        for mm in re.finditer(r"^\s*(\w+)\s*=\s*(\d+)", m.group(1), re.M):
            st[mm.group(1)] = int(mm.group(2))
    return {"ModFlag": dict(MODFLAG), "KeywordFlag": dict(KEYWORDFLAG), "SkillType": st}


class Unknown(dict):
    pass


FORMULA_FNS = {"m_min": "min", "m_max": "max", "math.min": "min", "math.max": "max", "round": "round",
               "m_floor": "floor", "math.floor": "floor", "m_ceil": "ceil", "math.ceil": "ceil", "tonumber": "num"}


def _is_formula(v):
    return isinstance(v, dict) and ("var" in v and len(v) == 1 or "op" in v or "fn" in v)


def _make(fname, args):
    if fname in FORMULA_FNS:
        return {"fn": FORMULA_FNS[fname], "args": list(args)}
    a = list(args) + [None] * 6
    if fname == "mod":
        tags = [x for x in args[5:] if isinstance(x, dict)]
        return {"k": "mod", "name": a[0], "type": a[1], "value": a[2], "flags": a[3] or 0, "kw": a[4] or 0,
                "tags": tags}
    if fname == "skill":
        tags = [x for x in args[2:] if isinstance(x, dict)]
        return {"k": "skill", "name": a[0], "value": a[1], "tags": tags}
    if fname == "flag":
        tags = [x for x in args[1:] if isinstance(x, dict)]
        return {"k": "flag", "name": a[0], "tags": tags}
    if fname in ("bit.bor", "bor"):
        v = 0
        for x in args:
            v |= int(x or 0)
        return v
    if fname in ("bit.band", "band"):
        v = -1
        for x in args:
            v &= int(x or 0)
        return v
    return Unknown(k="unknown", call=fname)


class Eval:
    def __init__(self, src, variables=()):
        self.p = Parser(src)
        self.variables = set(variables)

    def value(self):
        v = self.simple()
        p = self.p
        while p.peek().kind == "sym" and p.peek().val in ("+", "-", "*", "/", ".."):
            op = p.next().val
            r = self.simple()
            if isinstance(v, (int, float)) and isinstance(r, (int, float)) and not isinstance(v, bool):
                v = {"+": v + r, "-": v - r, "*": v * r, "/": (v / r) if r else 0}.get(op, v)
            elif (_is_formula(v) or _is_formula(r)) and op != "..":
                v = {"op": op, "a": v, "b": r}
            elif op == ".." and isinstance(v, str) and isinstance(r, str):
                v = v + r
            else:
                v = Unknown(k="unknown", expr="binop")
        return v

    def simple(self):
        p = self.p
        t = p.peek()
        if t.kind in ("str", "num"):
            p.next()
            return t.val
        if t.kind == "kw":
            if t.val in ("true", "false", "nil"):
                p.next()
                return {"true": True, "false": False, "nil": None}[t.val]
            if t.val == "function":
                p.skip_function()
                return Unknown(k="unknown", expr="function")
            if t.val == "not":
                p.next(); v = self.simple()
                return (not v) if isinstance(v, bool) else Unknown(k="unknown")
        if t.kind == "sym":
            if t.val == "{":
                return self.table()
            if t.val == "-":
                p.next(); v = self.simple()
                if _is_formula(v):
                    return {"op": "-", "a": 0, "b": v}
                return -v if isinstance(v, (int, float)) and not isinstance(v, bool) else Unknown(k="unknown")
            if t.val == "(":
                p.next(); v = self.value(); p.expect(")")
                return v
        if t.kind == "name":
            p.next()
            name = t.val
            while p.is_("."):
                p.next()
                name += "." + p.next().val
            if p.is_("("):
                p.next()
                args = []
                while not p.is_(")"):
                    args.append(self.value())
                    if p.is_(","):
                        p.next()
                p.expect(")")
                return _make(name, args)
            if name in self.variables:
                return {"var": name}
            if name.startswith("ModFlag."):
                return MODFLAG.get(name[8:], 0)
            if name.startswith("KeywordFlag."):
                return KEYWORDFLAG.get(name[12:], 0)
            if name.startswith("SkillType."):
                return name[10:]
            if p.is_("["):  # e.g. data.something[...]
                p.next(); self.value(); p.expect("]")
            return Unknown(k="unknown", ref=name)
        raise SyntaxError(f"unexpected {t!r}")

    def table(self):
        p = self.p
        p.expect("{")
        arr, dic = [], {}
        while not p.is_("}"):
            if p.is_("["):
                p.next(); k = self.value(); p.expect("]"); p.expect("=")
                dic[k] = self.value()
            elif p.peek().kind == "name" and p.is_("=", 1):
                k = p.next().val; p.next()
                dic[k] = self.value()
            else:
                arr.append(self.value())
            if p.is_(",") or p.is_(";"):
                p.next()
        p.expect("}")
        if dic:
            if arr:
                dic["_list"] = arr
            return dic
        return arr


def evaluate(src: str, variables=()):
    return Eval(src, variables).value()


def eval_formula(f, env):
    """Evaluate a formula produced with variables (e.g. {'fn':'min','args':[{'op':'-','a':{'var':'val'},'b':1},9]})."""
    import math
    if isinstance(f, dict):
        if "var" in f and len(f) == 1:
            return env.get(f["var"], 0)
        if "op" in f:
            a, b = eval_formula(f["a"], env), eval_formula(f["b"], env)
            return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0}[f["op"]]
        if "fn" in f:
            xs = [eval_formula(x, env) for x in f["args"]]
            fn = f["fn"]
            if fn == "min": return min(xs)
            if fn == "max": return max(xs)
            if fn == "round": return round(xs[0], int(xs[1]) if len(xs) > 1 else None) if len(xs) > 1 else math.floor(xs[0] + 0.5)
            if fn == "floor": return math.floor(xs[0])
            if fn == "ceil": return math.ceil(xs[0])
            if fn == "num": return float(xs[0])
        return {k: eval_formula(v, env) for k, v in f.items()}
    if isinstance(f, list):
        return [eval_formula(x, env) for x in f]
    return f


def clean(v):
    """Drop Unknown entries, strip helper types for JSON."""
    if isinstance(v, Unknown):
        return None
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean(x) for x in v if not isinstance(x, Unknown)]
    return v

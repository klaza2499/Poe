"""Safe reader for Lua *data* files (table literals) — never executes code.

Path of Building stores game data as Lua source like ``return { ... }`` or
``return function(x) x["A"] = { ... } end``. This module tokenizes the source
and parses table constructors into Python dicts/lists. Anything that is not a
plain literal (function calls like ``mod(...)``, ``SkillType.Spell``,
``function ... end`` bodies) is kept as a raw source string, so nothing from
the file is ever run.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SYMS = ["...", "..", "==", "~=", "<=", ">=", "//", "::", "<<", ">>",
         "{", "}", "[", "]", "(", ")", "=", ",", ";", ".", ":", "+", "-", "*",
         "/", "%", "^", "#", "<", ">", "&", "|", "~"]
_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for",
             "function", "goto", "if", "in", "local", "nil", "not", "or",
             "repeat", "return", "then", "true", "until", "while"}
_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f",
        "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


class Tok:
    __slots__ = ("kind", "val", "pos", "end")

    def __init__(self, kind, val, pos, end):
        self.kind, self.val, self.pos, self.end = kind, val, pos, end

    def __repr__(self):
        return f"Tok({self.kind},{self.val!r})"


def _long_bracket(src, i):
    """If src[i:] starts a long bracket ``[==[``, return (content, end)."""
    m = re.match(r"\[(=*)\[", src[i:i + 64])
    if not m:
        return None
    close = "]" + m.group(1) + "]"
    start = i + m.end()
    j = src.find(close, start)
    if j < 0:
        raise SyntaxError(f"unterminated long string at {i}")
    content = src[start:j]
    if content.startswith("\r\n"):
        content = content[2:]
    elif content.startswith("\n"):
        content = content[1:]
    return content, j + len(close)


def tokenize(src: str):
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("--", i):
            lb = _long_bracket(src, i + 2)
            if lb:
                i = lb[1]
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j + 1
            continue
        if c == "[" and (src.startswith("[[", i) or src.startswith("[=", i)):
            lb = _long_bracket(src, i)
            if lb:
                toks.append(Tok("str", lb[0], i, lb[1]))
                i = lb[1]
                continue
        if c in "\"'":
            j = i + 1
            out = []
            while True:
                if j >= n:
                    raise SyntaxError(f"unterminated string at {i}")
                ch = src[j]
                if ch == c:
                    j += 1
                    break
                if ch == "\\":
                    e = src[j + 1]
                    if e in _ESC:
                        out.append(_ESC[e]); j += 2
                    elif e.isdigit():
                        m = re.match(r"\d{1,3}", src[j + 1:])
                        out.append(chr(int(m.group()))); j += 1 + m.end()
                    elif e == "x":
                        out.append(chr(int(src[j + 2:j + 4], 16))); j += 4
                    elif e == "z":
                        j += 2
                        while j < n and src[j] in " \t\r\n":
                            j += 1
                    elif e == "u":
                        m = re.match(r"\{([0-9a-fA-F]+)\}", src[j + 2:])
                        out.append(chr(int(m.group(1), 16))); j += 2 + m.end()
                    else:
                        out.append(e); j += 2
                    continue
                out.append(ch)
                j += 1
            toks.append(Tok("str", "".join(out), i, j))
            i = j
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            m = _NUM.match(src, i)
            s = m.group()
            v = int(s, 16) if s[:2].lower() == "0x" else float(s)
            if isinstance(v, float) and v.is_integer() and not re.search(r"[.eE]", s):
                v = int(v)
            toks.append(Tok("num", v, i, m.end()))
            i = m.end()
            continue
        if c.isalpha() or c == "_":
            m = _NAME.match(src, i)
            w = m.group()
            toks.append(Tok("kw" if w in _KEYWORDS else "name", w, i, m.end()))
            i = m.end()
            continue
        for s in _SYMS:
            if src.startswith(s, i):
                toks.append(Tok("sym", s, i, i + len(s)))
                i += len(s)
                break
        else:
            raise SyntaxError(f"unexpected char {c!r} at {i}")
    toks.append(Tok("eof", None, n, n))
    return toks


class Raw(str):
    """Source text of a non-literal expression (kept, never evaluated)."""


_BINOPS = {"+", "-", "*", "/", "%", "^", "..", "==", "~=", "<", ">", "<=",
           ">=", "//", "&", "|", "~", "<<", ">>"}
_BLOCK_OPEN = {"function", "do", "then", "repeat"}


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.t = tokenize(src)
        self.i = 0

    # -- helpers
    def peek(self, k=0):
        return self.t[self.i + k]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def is_(self, val, k=0):
        tok = self.t[self.i + k]
        return tok.kind in ("sym", "kw") and tok.val == val

    def expect(self, val):
        tok = self.next()
        if tok.val != val:
            raise SyntaxError(f"expected {val!r} got {tok!r} near {self.src[tok.pos:tok.pos+60]!r}")
        return tok

    # -- skipping code we must not evaluate
    def skip_function(self):
        """Skip ``function (...) body end`` and return its raw source."""
        start = self.next().pos  # 'function'
        depth = 1
        while depth:
            tok = self.next()
            if tok.kind == "eof":
                raise SyntaxError("unterminated function")
            if tok.kind == "kw":
                if tok.val in ("function", "do", "repeat"):
                    depth += 1
                elif tok.val == "if":
                    depth += 1
                    # 'if' pairs with 'end'; its 'then' must not add depth
                elif tok.val in ("end", "until"):
                    depth -= 1
                elif tok.val in ("while", "for"):
                    pass  # their 'do' adds depth
        return Raw(self.src[start:self.t[self.i - 1].end])

    # -- expressions
    def value(self):
        start = self.peek().pos
        v = self.simple()
        while self.peek().kind in ("sym", "kw") and (self.peek().val in _BINOPS or self.peek().val in ("and", "or")):
            op = self.next().val
            rhs = self.simple()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(rhs, (int, float)):
                try:
                    v = {"+": v + rhs, "-": v - rhs, "*": v * rhs, "/": v / rhs if rhs else 0}.get(op, None)
                except Exception:
                    v = None
                if v is None:
                    v = Raw(self.src[start:self.t[self.i - 1].end])
            elif op == ".." and isinstance(v, str) and isinstance(rhs, str) and not isinstance(v, Raw) and not isinstance(rhs, Raw):
                v = v + rhs
            else:
                v = Raw(self.src[start:self.t[self.i - 1].end])
        return v

    def simple(self):
        tok = self.peek()
        if tok.kind == "str":
            self.next()
            return tok.val
        if tok.kind == "num":
            self.next()
            return tok.val
        if tok.kind == "kw":
            if tok.val == "true":
                self.next(); return True
            if tok.val == "false":
                self.next(); return False
            if tok.val == "nil":
                self.next(); return None
            if tok.val == "function":
                return self.skip_function()
            if tok.val == "not":
                self.next(); self.simple()
                return Raw(self.src[tok.pos:self.t[self.i - 1].end])
        if tok.kind == "sym":
            if tok.val == "{":
                return self.table()
            if tok.val == "-":
                self.next()
                v = self.simple()
                return -v if isinstance(v, (int, float)) and not isinstance(v, bool) else Raw(self.src[tok.pos:self.t[self.i - 1].end])
            if tok.val == "#":
                self.next(); self.simple()
                return Raw(self.src[tok.pos:self.t[self.i - 1].end])
            if tok.val == "(":
                self.next(); v = self.value(); self.expect(")")
                return self.suffixes(tok.pos, v)
        if tok.kind == "name":
            self.next()
            return self.suffixes(tok.pos, Raw(tok.val))
        raise SyntaxError(f"unexpected {tok!r} near {self.src[tok.pos:tok.pos+80]!r}")

    def suffixes(self, start, v):
        changed = False
        while True:
            if self.is_("."):
                self.next(); self.next(); changed = True
            elif self.is_("[") and not self.is_("[", 1):
                self.next(); self.value(); self.expect("]"); changed = True
            elif self.is_(":"):
                self.next(); self.next(); self.call_args(); changed = True
            elif self.is_("(") or self.peek().kind == "str" or self.is_("{"):
                self.call_args(); changed = True
            else:
                break
        if changed or isinstance(v, Raw):
            return Raw(self.src[start:self.t[self.i - 1].end])
        return v

    def call_args(self):
        if self.peek().kind == "str":
            self.next(); return
        if self.is_("{"):
            self.table(); return
        self.expect("(")
        while not self.is_(")"):
            self.value()
            if self.is_(","):
                self.next()
        self.expect(")")

    def table(self):
        self.expect("{")
        arr, dic = [], {}
        while not self.is_("}"):
            if self.is_("["):
                self.next(); k = self.value(); self.expect("]"); self.expect("=")
                dic[k if not isinstance(k, float) or not k.is_integer() else int(k)] = self.value()
            elif self.peek().kind == "name" and self.is_("=", 1):
                k = self.next().val; self.next()
                dic[k] = self.value()
            else:
                arr.append(self.value())
            if self.is_(",") or self.is_(";"):
                self.next()
        self.expect("}")
        if dic and arr:
            for idx, v in enumerate(arr, 1):
                dic.setdefault(idx, v)
            return dic
        if dic:
            # pure integer keys 1..n -> list
            if all(isinstance(k, int) for k in dic) and sorted(dic) == list(range(1, len(dic) + 1)):
                return [dic[k] for k in range(1, len(dic) + 1)]
            return dic
        return arr


def parse_return_table(src: str):
    """Parse a file of the form ``return { ... }``."""
    p = Parser(src)
    while not p.is_("return"):
        p.next()
    p.next()
    return p.value()


def parse_assignments(src: str, target: str):
    """Collect ``target["key"] = <value>`` statements anywhere in the file
    (e.g. ``itemBases["Paua Amulet"] = {...}`` or ``skills["Arc"] = {...}``).
    Other statements are skipped token by token without evaluation."""
    p = Parser(src)
    out = {}
    while p.peek().kind != "eof":
        tok = p.peek()
        if tok.kind == "name" and tok.val == target and p.is_("[", 1):
            p.next(); p.next()
            key = p.value()
            p.expect("]")
            if p.is_("="):
                p.next()
                out[key] = p.value()
                continue
        p.next()
    return out


def plain(v):
    """Convert Raw to plain str recursively (for JSON)."""
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    if isinstance(v, Raw):
        return str(v)
    return v

"""Passive tree logic: path finding, allocation, point counting."""
from __future__ import annotations

from collections import deque

from . import data



def node(nid):
    return data.nodes().get(int(nid))


def is_ascendancy(n):
    return bool(n.get("ascendancy"))


def costs_point(n):
    return n["type"] not in ("classStart", "ascendancyStart")


def find_nodes(query, kind=None, ascendancy=None, limit=30):
    """Search nodes by name or stat text. kind: normal/notable/keystone/mastery/jewel."""
    words = data.norm(query).split()
    out = []
    for nid, n in data.nodes().items():
        if n.get("isProxy") or n.get("isClusterInner"):
            continue
        if kind and n["type"] != kind:
            continue
        if ascendancy is not None and (n.get("ascendancy") or "") != ascendancy:
            continue
        hay = data.norm(n["name"] + " " + " ".join(n.get("stats", [])) + " " +
                        " ".join(" ".join(m["stats"]) for m in n.get("masteryEffects", [])))
        if all(w in hay for w in words):
            out.append(nid)
    # notables/keystones first
    rank = {"keystone": 0, "notable": 1, "mastery": 2, "jewel": 3, "normal": 4}
    def key(i):
        n = data.nodes()[i]
        nm = data.norm(n["name"])
        return (nm != " ".join(words), not all(w in nm for w in words), rank.get(n["type"], 5), n["name"])
    out.sort(key=key)  # exact name, then name matches, then stat-text matches
    return out[:limit]


def resolve_node(ref, ascendancy=None):
    """Accept a node id (int/str) or an exact/unique name."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        nid = int(ref)
        if nid in data.nodes():
            return nid
        raise KeyError(f"no node with id {nid}")
    n = data.norm(ref)
    exact = [nid for nid, x in data.nodes().items() if data.norm(x["name"]) == n
             and not x.get("isProxy") and not x.get("isClusterInner")]
    if ascendancy and len(exact) > 1:
        pref = [i for i in exact if data.nodes()[i].get("ascendancy") == ascendancy]
        exact = pref or exact
    # prefer notable/keystone over small nodes that share a name
    if len(exact) > 1:
        big = [i for i in exact if data.nodes()[i]["type"] in ("notable", "keystone", "jewel")]
        if len(big) == 1:
            return big[0]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise KeyError(f"'{ref}' is ambiguous ({len(exact)} nodes: {exact[:10]}); use a node id")
    raise KeyError(f"no node named '{ref}' (try search_passives)")


class TreeSpec:
    """Allocated passive set for one character."""

    def __init__(self, class_id, ascend_id=0, nodes=None, masteries=None, bloodline=None):
        self.class_id = class_id
        self.ascend_id = ascend_id
        self.bloodline = bloodline  # secondary ascendancy id, e.g. "Warden"
        self.alloc = set(int(n) for n in (nodes or []))
        self.masteries = {int(k): int(v) for k, v in (masteries or {}).items()}
        self.alloc.add(self.start)
        if self.asc_start:
            self.alloc.add(self.asc_start)
        if self.bloodline_start:
            self.alloc.add(self.bloodline_start)

    # -- class info
    @property
    def cls(self):
        return data.classes()[self.class_id]

    @property
    def asc(self):
        if not self.ascend_id:
            return None
        return self.cls["ascendancies"][self.ascend_id - 1]

    @property
    def start(self):
        return self.cls["startNode"]

    @property
    def asc_start(self):
        return self.asc["startNode"] if self.asc else None

    @property
    def bloodline_start(self):
        if not self.bloodline:
            return None
        for b in data.tree()["bloodlines"]:
            if b["id"] == self.bloodline:
                return b["startNode"]
        return None

    def _allowed_asc(self):
        s = set()
        if self.asc:
            s.add(self.asc["id"])
        if self.bloodline:
            s.add(self.bloodline)
        return s

    def traversable(self, nid):
        n = node(nid)
        if n is None or n.get("isProxy") or n.get("isClusterInner"):
            return False
        if n["type"] == "mastery":
            return False
        if n["type"] == "classStart":
            return nid == self.start
        if n.get("ascendancy"):
            return n["ascendancy"] in self._allowed_asc()
        return True

    # -- path finding
    def path_to(self, target):
        """Shortest list of unallocated nodes needed to reach target (inclusive)."""
        target = int(target)
        if target in self.alloc:
            return []
        t = node(target)
        if t is None:
            raise KeyError(f"node {target} not found")
        if t["type"] == "mastery":
            if any(node(a) and node(a).get("group") == t.get("group") for a in self.alloc if a != self.start):
                return [target]
            # path to nearest node of the same group, then the mastery
            group_nodes = [i for i, x in data.nodes().items() if x.get("group") == t.get("group") and i != target
                           and self.traversable(i)]
            best = None
            for g in group_nodes:
                p = self.path_to(g)
                if best is None or len(p) < len(best):
                    best = p
            if best is None:
                raise ValueError("mastery group unreachable")
            return best + [target]
        if not self.traversable(target):
            raise ValueError(f"node {target} ({t['name']}) is not reachable for this class/ascendancy")
        prev = {a: None for a in self.alloc}
        q = deque(self.alloc)
        while q:
            cur = q.popleft()
            for nb in node(cur)["adj"]:
                if nb in prev or not self.traversable(nb):
                    continue
                # never walk from main tree into an ascendancy (or back) except through its start node
                if bool(node(cur).get("ascendancy")) != bool(node(nb).get("ascendancy")):
                    continue
                prev[nb] = cur
                if nb == target:
                    path = []
                    x = nb
                    while x is not None and x not in self.alloc:
                        path.append(x)
                        x = prev[x]
                    return list(reversed(path))
                q.append(nb)
        raise ValueError(f"no path to {target} ({t['name']})")

    def allocate(self, target, mastery_effect=None):
        path = self.path_to(target)
        self.alloc.update(path)
        t = node(target)
        if t["type"] == "mastery":
            effs = [m["effect"] for m in t.get("masteryEffects", [])]
            if mastery_effect is None:
                raise ValueError(f"mastery {t['name']} needs an effect id: {effs}")
            if int(mastery_effect) not in effs:
                raise ValueError(f"effect {mastery_effect} not on {t['name']}: {effs}")
            self.masteries[int(target)] = int(mastery_effect)
        return path

    def deallocate(self, target):
        """Remove a node and every node that becomes disconnected from the start."""
        target = int(target)
        if target in (self.start, self.asc_start, self.bloodline_start):
            raise ValueError("cannot remove a start node")
        self.alloc.discard(target)
        self.masteries.pop(target, None)
        roots = [r for r in (self.start, self.asc_start, self.bloodline_start) if r]
        seen = set(roots)
        q = deque(roots)
        while q:
            cur = q.popleft()
            for nb in node(cur)["adj"]:
                if nb in self.alloc and nb not in seen:
                    seen.add(nb)
                    q.append(nb)
        # masteries stay if a group-mate is still allocated
        for m in list(self.alloc - seen):
            n = node(m)
            if n["type"] == "mastery" and any(node(a).get("group") == n.get("group") for a in seen if node(a)):
                seen.add(m)
        removed = sorted(self.alloc - seen)
        self.alloc = seen
        for r in removed:
            self.masteries.pop(r, None)
        return [target] + removed

    # -- counts
    def counts(self):
        main = asc = blood = 0
        for nid in self.alloc:
            n = node(nid)
            if not costs_point(n):
                continue
            a = n.get("ascendancy")
            if a and self.bloodline and a == self.bloodline:
                blood += 1
            elif a:
                asc += 1
            else:
                main += 1
        return {"passives": main, "ascendancy": asc, "bloodline": blood}

    def jewel_sockets(self):
        return sorted(n for n in self.alloc if node(n)["type"] == "jewel")

    def stat_lines(self):
        out = []
        for nid in sorted(self.alloc):
            n = node(nid)
            if n["type"] == "mastery":
                eff = self.masteries.get(nid)
                for m in n.get("masteryEffects", []):
                    if m["effect"] == eff:
                        out += [(nid, s) for s in m["stats"]]
            else:
                out += [(nid, s) for s in n.get("stats", [])]
        return out

    def granted_attributes(self):
        s = d = i = 0
        for nid in self.alloc:
            n = node(nid)
            s += n.get("grantedStr", 0)
            d += n.get("grantedDex", 0)
            i += n.get("grantedInt", 0)
        return s, d, i

    def keystones(self):
        return [node(n)["name"] for n in sorted(self.alloc) if node(n)["type"] == "keystone"]

    def notables(self):
        return [node(n)["name"] for n in sorted(self.alloc) if node(n)["type"] == "notable"]


def points_available(level, bandit="None"):
    """Main-tree points at a character level, assuming all passive quests done."""
    pts = (level - 1) + 23  # 23 points from quests (Acts 1-10)
    if bandit in (None, "", "None", "KillAll", "Kill all"):
        pts += 1  # killing all bandits: +1 point (99 + 23 + 1 = 123 at level 100, same as PoB)
    return pts

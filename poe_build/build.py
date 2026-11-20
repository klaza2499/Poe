"""Build model (saved as JSON in builds/) + Path of Building import/export codes."""
from __future__ import annotations

import base64
import json
import os
import re
import xml.etree.ElementTree as ET
import zlib

from . import data, items as itemsmod, skills as skillsmod
from .tree import TreeSpec, resolve_node

BUILDS_DIR = os.environ.get("POE_BUILDS_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "builds")

BANDITS = ["None", "Alira", "Kraityn", "Oak"]


def safe_name(name: str) -> str:
    n = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip().replace(" ", "_")
    if not n:
        raise ValueError("empty build name")
    return n


class Build:
    def __init__(self, name, class_name="Witch", ascendancy=None, level=90):
        self.name = name
        c, a = data.find_class(ascendancy or class_name)
        if c is None:
            raise KeyError(f"unknown class/ascendancy '{ascendancy or class_name}'")
        if a is None and ascendancy:
            raise KeyError(f"unknown ascendancy '{ascendancy}'")
        self.class_name = c["name"]
        self.ascendancy = a["id"] if a else None
        self.bloodline = None
        self.level = int(level)
        self.bandit = "None"
        self.pantheon_major = None
        self.pantheon_minor = None
        self.tree_nodes: list[int] = []
        self.masteries: dict[int, int] = {}
        self.items: dict[str, str] = {}
        self.jewels: dict[int, str] = {}
        self.skill_groups: list[dict] = []
        self.main_group = 1
        self.notes = ""
        self.config: dict = {}

    # ------------------------------------------------ helpers
    @property
    def class_id(self):
        return next(c["classId"] for c in data.classes() if c["name"] == self.class_name)

    @property
    def ascend_id(self):
        c = data.classes()[self.class_id]
        for a in c["ascendancies"]:
            if a["id"] == self.ascendancy:
                return a["ascendClassId"]
        return 0

    def spec(self) -> TreeSpec:
        return TreeSpec(self.class_id, self.ascend_id, self.tree_nodes, self.masteries, self.bloodline)

    def _store_spec(self, sp: TreeSpec):
        self.tree_nodes = sorted(sp.alloc)
        self.masteries = dict(sp.masteries)

    # ------------------------------------------------ tree ops
    def allocate(self, refs, mastery_effect=None):
        sp = self.spec()
        added = []
        for ref in refs if isinstance(refs, list) else [refs]:
            nid = resolve_node(ref, self.ascendancy)
            added += sp.allocate(nid, mastery_effect)
        self._store_spec(sp)
        return added

    def deallocate(self, refs):
        sp = self.spec()
        removed = []
        for ref in refs if isinstance(refs, list) else [refs]:
            nid = resolve_node(ref, self.ascendancy)
            if nid in sp.alloc:
                removed += sp.deallocate(nid)
        self._store_spec(sp)
        for nid in removed:
            self.jewels.pop(nid, None)
        return removed

    def reset_tree(self):
        self.tree_nodes, self.masteries, self.jewels = [], {}, {}

    def set_class(self, class_name, ascendancy=None):
        c, a = data.find_class(ascendancy or class_name)
        if c is None:
            raise KeyError(f"unknown class '{class_name}'")
        if c["name"] != self.class_name:
            self.reset_tree()
        self.class_name = c["name"]
        old = self.ascendancy
        self.ascendancy = a["id"] if a else None
        if old != self.ascendancy:
            self.tree_nodes = [n for n in self.tree_nodes if not data.nodes()[n].get("ascendancy")
                               or data.nodes()[n].get("ascendancy") == self.bloodline]

    def set_bloodline(self, bloodline):
        if not bloodline or data.norm(bloodline) == "none":
            old = self.bloodline
            self.bloodline = None
            self.tree_nodes = [n for n in self.tree_nodes if data.nodes()[n].get("ascendancy") != old]
            return
        for b in data.tree()["bloodlines"]:
            if data.norm(b["id"]) == data.norm(bloodline) or data.norm(b["name"]) == data.norm(bloodline):
                self.bloodline = b["id"]
                return
        raise KeyError(f"unknown bloodline. Options: {[b['name'] for b in data.tree()['bloodlines']]}")

    # ------------------------------------------------ items
    def equip(self, slot, text):
        if slot.strip().isdigit() or slot.lower().startswith("jewel"):
            nid = int(re.sub(r"\D", "", slot))
            n = data.nodes().get(nid)
            if not n or n["type"] != "jewel":
                raise ValueError(f"{nid} is not a jewel socket")
            if nid not in self.tree_nodes:
                raise ValueError(f"jewel socket {nid} is not allocated (allocate it first)")
            self.jewels[nid] = text
            return f"jewel socket {nid}"
        slot = itemsmod.canon_slot(slot)
        t = itemsmod.item_type(text)
        if t and not itemsmod.slot_ok(slot, t):
            raise ValueError(f"a {t} cannot go in {slot}")
        self.items[slot] = text
        return slot

    def unequip(self, slot):
        if slot.strip().isdigit():
            return self.jewels.pop(int(slot), None) is not None
        return self.items.pop(itemsmod.canon_slot(slot), None) is not None

    # ------------------------------------------------ skills
    def add_group(self, gems, slot=None, label=None, main=False):
        g = {"slot": itemsmod.canon_slot(slot) if slot else None, "label": label, "enabled": True, "gems": []}
        for x in gems:
            if isinstance(x, str):
                x = {"name": x}
            gd = skillsmod.resolve_gem(x["name"])
            lvl = x.get("level") or (20 if gd["maxLevel"] >= 20 else gd["maxLevel"])
            g["gems"].append({"name": gd["name"], "level": int(lvl), "quality": int(x.get("quality", 20)),
                              "enabled": x.get("enabled", True)})
        self.skill_groups.append(g)
        if main or len(self.skill_groups) == 1:
            self.main_group = len(self.skill_groups)
        return len(self.skill_groups), skillsmod.check_group(g)

    def remove_group(self, index):
        self.skill_groups.pop(index - 1)
        self.main_group = min(self.main_group, max(1, len(self.skill_groups)))

    # ------------------------------------------------ serialization
    def to_dict(self):
        return {
            "name": self.name, "class": self.class_name, "ascendancy": self.ascendancy,
            "bloodline": self.bloodline, "level": self.level, "bandit": self.bandit,
            "pantheonMajor": self.pantheon_major, "pantheonMinor": self.pantheon_minor,
            "treeVersion": data.tree()["version"],
            "tree": {"nodes": self.tree_nodes, "masteries": {str(k): v for k, v in self.masteries.items()}},
            "items": self.items, "jewels": {str(k): v for k, v in self.jewels.items()},
            "skills": self.skill_groups, "mainSkill": self.main_group, "notes": self.notes, "config": self.config,
        }

    @classmethod
    def from_dict(cls, d):
        b = cls(d["name"], d.get("class", "Witch"), d.get("ascendancy"), d.get("level", 90))
        b.bloodline = d.get("bloodline")
        b.bandit = d.get("bandit", "None")
        b.pantheon_major = d.get("pantheonMajor")
        b.pantheon_minor = d.get("pantheonMinor")
        t = d.get("tree", {})
        b.tree_nodes = [int(n) for n in t.get("nodes", [])]
        b.masteries = {int(k): int(v) for k, v in t.get("masteries", {}).items()}
        b.items = dict(d.get("items", {}))
        b.jewels = {int(k): v for k, v in d.get("jewels", {}).items()}
        b.skill_groups = list(d.get("skills", []))
        b.main_group = d.get("mainSkill", 1)
        b.notes = d.get("notes", "")
        b.config = d.get("config", {})
        return b

    def path(self):
        return os.path.join(BUILDS_DIR, safe_name(self.name) + ".json")

    def save(self):
        os.makedirs(BUILDS_DIR, exist_ok=True)
        self.tree_nodes = sorted(self.spec().alloc)
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        return self.path()

    @classmethod
    def load(cls, name):
        p = name if name.endswith(".json") and os.path.exists(name) else os.path.join(BUILDS_DIR, safe_name(name) + ".json")
        if not os.path.exists(p):
            raise FileNotFoundError(f"build '{name}' not found. Existing: {list_builds()}")
        with open(p, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------ summary
    def summary(self, full=False, with_calc=False):
        from . import stats
        sp = self.spec()
        st = stats.compute(self)
        groups = []
        for i, g in enumerate(self.skill_groups, 1):
            groups.append({"index": i, "slot": g.get("slot"), "label": g.get("label"),
                           "gems": [f"{x['name']} {x['level']}/{x['quality']}" for x in g["gems"]],
                           "main": i == self.main_group, "warnings": skillsmod.check_group(g)})
        out = {
            "name": self.name, "class": self.class_name, "ascendancy": self.ascendancy, "bloodline": self.bloodline,
            "level": self.level, "bandit": self.bandit, "points": st["points"],
            "keystones": sp.keystones(), "notables": sp.notables(),
            "ascendancy_nodes": [data.nodes()[n]["name"] for n in sorted(sp.alloc)
                                 if data.nodes()[n].get("ascendancy") and data.nodes()[n]["type"] != "ascendancyStart"],
            "masteries": {data.nodes()[k]["name"]: next((" / ".join(m["stats"]) for m in data.nodes()[k].get("masteryEffects", [])
                                                         if m["effect"] == v), v) for k, v in sp.masteries.items()},
            "jewel_sockets": {str(n): (itemsmod.parse_item(self.jewels[n])["name"] if n in self.jewels else None)
                              for n in sp.jewel_sockets()},
            "items": {s: _item_label(t) for s, t in self.items.items()},
            "skills": groups,
            "stats": {k: st[k] for k in ("attributes", "life", "mana", "energy_shield", "armour", "evasion", "resistances")},
            "dps": {k: st["offence"].get(k) for k in ("skill", "total_dps", "hit_dps", "dot_dps", "average_hit",
                                                        "uses_per_second", "crit_chance", "hit_chance") if k in st["offence"]}
                   | {k: st["offence"][k]["dps"] for k in ("ignite", "bleed", "poison") if k in st["offence"]},
            "defence": {k: st["defence"].get(k) for k in ("block", "spell_suppression", "max_hit_taken", "mana_unreserved",
                                                          "life_regen", "movement_speed")},
            "problems": st["problems"],
            "notes": self.notes,
        }
        if with_calc:
            out["offence"] = st["offence"]
            out["defence_full"] = st["defence"]
            out["buffs"] = st["buffs"]
            out["calc_notes"] = st["notes"]
            out["unparsed_mods"] = st["unparsed_mods"]
            out["unparsed_count"] = st["unparsed_count"]
        if full:
            out["stat_totals"] = st["stat_totals"]
            out["item_text"] = self.items
        return out

    # ------------------------------------------------ PoB XML
    def to_pob_xml(self) -> str:
        root = ET.Element("PathOfBuilding")
        ET.SubElement(root, "Build", {
            "level": str(self.level), "targetVersion": "3_0", "className": self.class_name,
            "ascendClassName": self.ascendancy or "None", "bandit": self.bandit or "None",
            "mainSocketGroup": str(self.main_group), "viewMode": "TREE",
            "pantheonMajorGod": self.pantheon_major or "None", "pantheonMinorGod": self.pantheon_minor or "None",
        })
        ET.SubElement(root, "Import")
        sk = ET.SubElement(root, "Skills", {"activeSkillSet": "1", "defaultGemLevel": "normalMaximum",
                                            "defaultGemQuality": "0", "sortGemsByDPS": "true"})
        ss = ET.SubElement(sk, "SkillSet", {"id": "1"})
        for g in self.skill_groups:
            attrs = {"enabled": str(g.get("enabled", True)).lower(), "includeInFullDPS": "false",
                     "mainActiveSkill": "1", "mainActiveSkillCalcs": "1"}
            if g.get("slot"):
                attrs["slot"] = g["slot"]
            if g.get("label"):
                attrs["label"] = g["label"]
            se = ET.SubElement(ss, "Skill", attrs)
            for gem in g["gems"]:
                gd = data.find_gem(gem["name"]) or {}
                ET.SubElement(se, "Gem", {
                    "nameSpec": gd.get("name", gem["name"]), "skillId": gd.get("skillId", ""),
                    "gemId": gd.get("gemId", ""), "variantId": gd.get("variantId", ""),
                    "level": str(gem.get("level", 20)), "quality": str(gem.get("quality", 0)),
                    "enabled": str(gem.get("enabled", True)).lower(), "enableGlobal1": "true",
                    "enableGlobal2": "false", "count": "1",
                })
        bloodline_id = 0
        if self.bloodline:
            for i, x in enumerate(data.tree()["bloodlines"], 1):
                if x["id"] == self.bloodline:
                    bloodline_id = i
        tr = ET.SubElement(root, "Tree", {"activeSpec": "1"})
        sp = self.spec()
        spec = ET.SubElement(tr, "Spec", {
            "title": self.name, "treeVersion": data.tree()["version"], "classId": str(self.class_id),
            "ascendClassId": str(self.ascend_id), "secondaryAscendClassId": str(bloodline_id),
            "nodes": ",".join(str(n) for n in sorted(sp.alloc)),
            "masteryEffects": ",".join(f"{{{k},{v}}}" for k, v in sorted(sp.masteries.items())),
            "clusterHashFormatVersion": "2",
        })
        socks = ET.SubElement(spec, "Sockets")
        it_el = ET.SubElement(root, "Items", {"activeItemSet": "1", "useSecondWeaponSet": "false"})
        item_id = 0
        slot_ids = {}
        for slot, text in self.items.items():
            item_id += 1
            e = ET.SubElement(it_el, "Item", {"id": str(item_id)})
            e.text = "\n" + text.strip() + "\n"
            slot_ids[slot] = item_id
        for nid, text in self.jewels.items():
            item_id += 1
            e = ET.SubElement(it_el, "Item", {"id": str(item_id)})
            e.text = "\n" + text.strip() + "\n"
            ET.SubElement(socks, "Socket", {"nodeId": str(nid), "itemId": str(item_id)})
        iset = ET.SubElement(it_el, "ItemSet", {"id": "1", "useSecondWeaponSet": "false"})
        for slot, iid in slot_ids.items():
            ET.SubElement(iset, "Slot", {"name": slot, "itemId": str(iid)})
        n = ET.SubElement(root, "Notes")
        n.text = self.notes or ""
        cfg = ET.SubElement(root, "Config")
        for k, v in (self.config or {}).items():
            a = {"name": k}
            if isinstance(v, bool):
                a["boolean"] = str(v).lower()
            elif isinstance(v, (int, float)):
                a["number"] = str(v)
            else:
                a["string"] = str(v)
            ET.SubElement(cfg, "Input", a)
        return ET.tostring(root, encoding="unicode")

    def export_code(self) -> str:
        raw = zlib.compress(self.to_pob_xml().encode("utf-8"), 9)
        return base64.b64encode(raw).decode().replace("+", "-").replace("/", "_")

    # ------------------------------------------------ import
    @classmethod
    def from_pob_code(cls, code: str, name: str | None = None):
        code = code.strip()
        code = re.sub(r"\s+", "", code)
        raw = base64.b64decode(code.replace("-", "+").replace("_", "/") + "=" * (-len(code) % 4))
        return cls.from_pob_xml(zlib.decompress(raw).decode("utf-8", "replace"), name)

    @classmethod
    def from_pob_xml(cls, xml_text: str, name: str | None = None):
        root = ET.fromstring(xml_text)
        be = root.find("Build")
        cname = be.get("className", "Scion")
        asc = be.get("ascendClassName")
        b = cls(name or "Imported", cname, asc if asc and asc != "None" else None, int(be.get("level", 90)))
        b.bandit = be.get("bandit", "None")
        b.pantheon_major = be.get("pantheonMajorGod")
        b.pantheon_minor = be.get("pantheonMinorGod")
        b.main_group = int(be.get("mainSocketGroup", 1) or 1)
        warnings = []
        sock_map = {}
        tr = root.find("Tree")
        if tr is not None:
            act = tr.get("activeSpec", "1")
            specs = tr.findall("Spec")
            spec = specs[int(act) - 1] if act.isdigit() and int(act) <= len(specs) else (specs[0] if specs else None)
            if spec is not None:
                if spec.get("treeVersion") and spec.get("treeVersion") != data.tree()["version"]:
                    warnings.append(f"build tree version {spec.get('treeVersion')} != data {data.tree()['version']}; unknown nodes dropped")
                ids = [int(x) for x in re.findall(r"\d+", spec.get("nodes", ""))]
                known = [i for i in ids if i in data.nodes()]
                if len(known) != len(ids):
                    warnings.append(f"{len(ids) - len(known)} node ids not in current tree (cluster jewel nodes etc.)")
                b.tree_nodes = known
                b.masteries = {int(m): int(e) for m, e in re.findall(r"\{(\d+),(\d+)\}", spec.get("masteryEffects", ""))}
                sec = int(spec.get("secondaryAscendClassId", 0) or 0)
                if sec and sec <= len(data.tree()["bloodlines"]):
                    b.bloodline = data.tree()["bloodlines"][sec - 1]["id"]
                socks = spec.find("Sockets")
                if socks is not None:
                    for s in socks.findall("Socket"):
                        sock_map[s.get("itemId")] = int(s.get("nodeId"))
        items = {}
        ie = root.find("Items")
        if ie is not None:
            for it in ie.findall("Item"):
                items[it.get("id")] = (it.text or "").strip()
            sets = ie.findall("ItemSet")
            active = ie.get("activeItemSet", "1")
            iset = next((s for s in sets if s.get("id") == active), sets[0] if sets else None)
            if iset is not None:
                for sl in iset.findall("Slot"):
                    iid = sl.get("itemId")
                    if iid and iid != "0" and iid in items:
                        nm = sl.get("name")
                        if nm in itemsmod.SLOTS:
                            b.items[nm] = _clean_item(items[iid])
            for iid, nid in sock_map.items():
                if iid in items:
                    b.jewels[nid] = _clean_item(items[iid])
        sk = root.find("Skills")
        if sk is not None:
            sets = sk.findall("SkillSet") or [sk]
            act = sk.get("activeSkillSet", "1")
            sset = next((s for s in sets if s.get("id") == act), sets[0])
            for s in sset.findall("Skill"):
                g = {"slot": s.get("slot"), "label": s.get("label"), "enabled": s.get("enabled", "true") == "true", "gems": []}
                for gm in s.findall("Gem"):
                    nm = gm.get("nameSpec") or gm.get("skillId")
                    g["gems"].append({"name": nm, "level": int(gm.get("level", 20)), "quality": int(gm.get("quality", 0)),
                                      "enabled": gm.get("enabled", "true") == "true"})
                if g["gems"]:
                    b.skill_groups.append(g)
        notes = root.find("Notes")
        if notes is not None and notes.text:
            b.notes = notes.text.strip()
        cfg = root.find("Config")
        if cfg is not None:
            for inp in cfg.iter("Input"):
                v = inp.get("boolean") or inp.get("number") or inp.get("string")
                if inp.get("boolean"):
                    v = inp.get("boolean") == "true"
                elif inp.get("number"):
                    v = float(inp.get("number"))
                b.config[inp.get("name")] = v
        b._import_warnings = warnings
        return b


def _clean_item(text):
    lines = [l.strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


def _item_label(text):
    it = itemsmod.parse_item(text)
    if it["rarity"] in ("UNIQUE", "RELIC"):
        return f"{it['name']} ({it['base']})"
    return f"{it['rarity'].title()} {it['base']}" + (f" '{it['name']}'" if it["rarity"] == "RARE" else "")


def list_builds():
    if not os.path.isdir(BUILDS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(BUILDS_DIR) if f.endswith(".json"))


def delete_build(name):
    p = os.path.join(BUILDS_DIR, safe_name(name) + ".json")
    if os.path.exists(p):
        os.remove(p)
        return True
    return False

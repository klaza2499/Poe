"""Import a character from pathofexile.com (public profile), the same endpoints PoB uses."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from . import data
from .build import Build

HOST = "https://www.pathofexile.com/"
RARITY = {0: "NORMAL", 1: "MAGIC", 2: "RARE", 3: "UNIQUE", 9: "RELIC", 10: "RELIC", 15: "NORMAL"}
SLOT_MAP = {"Weapon": "Weapon 1", "Offhand": "Weapon 2", "Weapon2": "Weapon 1 Swap", "Offhand2": "Weapon 2 Swap",
            "Helm": "Helmet", "BodyArmour": "Body Armour", "Gloves": "Gloves", "Boots": "Boots", "Amulet": "Amulet",
            "Ring": "Ring 1", "Ring2": "Ring 2", "Belt": "Belt"}


def _get(path, params):
    url = HOST + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "poe-build-planner/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8")
    if body.strip() == "false":
        raise PermissionError("character not found or profile is private (Privacy settings > show characters)")
    return json.loads(body)


def _clean(s):
    return re.sub(r"<<set:\w+>>", "", s or "").strip()


def item_text(it: dict) -> str:
    """GGG item JSON -> PoB item text."""
    rarity = RARITY.get(it.get("frameType"), "NORMAL")
    lines = [f"Rarity: {rarity}"]
    name, type_line = _clean(it.get("name")), _clean(it.get("typeLine"))
    base = _clean(it.get("baseType")) or type_line
    if name:
        lines += [name, base]
    else:
        lines.append(type_line)
    for inf, label in (("shaper", "Shaper"), ("elder", "Elder"), ("crusader", "Crusader"), ("redeemer", "Redeemer"),
                       ("hunter", "Hunter"), ("warlord", "Warlord")):
        if (it.get("influences") or {}).get(inf):
            lines.append(f"{label} Item")
    if it.get("ilvl"):
        lines.append(f"Item Level: {it['ilvl']}")
    for p in it.get("properties") or []:
        if p.get("name") == "Quality" and p.get("values"):
            q = re.search(r"\d+", p["values"][0][0])
            if q:
                lines.append(f"Quality: {q.group()}")
        if p.get("name") == "Radius" and p.get("values"):
            lines.append(f"Radius: {p['values'][0][0]}")
        if p.get("name") == "Limited to" and p.get("values"):
            lines.append(f"Limited to: {p['values'][0][0]}")
    if it.get("sockets"):
        groups = {}
        for s in it["sockets"]:
            groups.setdefault(s.get("group", 0), []).append(s.get("sColour", "W"))
        lines.append("Sockets: " + " ".join("-".join(g) for _, g in sorted(groups.items())))
    impl = [_clean(m) for m in (it.get("enchantMods") or [])] + [_clean(m) for m in (it.get("implicitMods") or [])]
    lines.append(f"Implicits: {len(impl)}")
    lines += impl
    for key, tag in (("fracturedMods", ""), ("explicitMods", ""), ("craftedMods", "{crafted}"), ("crucibleMods", "")):
        for m in it.get(key) or []:
            for sub in _clean(m).split("\n"):
                lines.append(tag + sub)
    if it.get("corrupted"):
        lines.append("Corrupted")
    if it.get("mirrored"):
        lines.append("Mirrored")
    return "\n".join(lines)


def _gem_groups(items):
    groups = []
    for it in items:
        slot = SLOT_MAP.get(it.get("inventoryId"))
        if not slot or "Swap" in slot or not it.get("socketedItems"):
            continue
        link = {i: s.get("group", 0) for i, s in enumerate(it.get("sockets") or [])}
        by_group = {}
        for g in it["socketedItems"]:
            if g.get("abyssJewel"):
                continue
            lvl, q = 20, 0
            for p in g.get("properties") or []:
                if p.get("name") == "Level" and p.get("values"):
                    m = re.search(r"\d+", p["values"][0][0])
                    lvl = int(m.group()) if m else 20
                if p.get("name") == "Quality" and p.get("values"):
                    m = re.search(r"\d+", p["values"][0][0])
                    q = int(m.group()) if m else 0
            name = _clean(g.get("typeLine"))
            gd = data.find_gem(name)
            by_group.setdefault(link.get(g.get("socket", 0), 0), []).append(
                {"name": gd["name"] if gd else name, "level": lvl, "quality": q, "enabled": True})
        for _, gems in sorted(by_group.items()):
            groups.append({"slot": slot, "label": None, "enabled": True, "gems": gems})
    return groups


def import_character(account: str, character: str, name: str | None = None, realm: str = "pc") -> Build:
    items = _get("character-window/get-items", {"accountName": account, "character": character, "realm": realm})
    passives = _get("character-window/get-passive-skills", {"accountName": account, "character": character, "realm": realm})
    ch = items.get("character") or {}
    asc = ch.get("ascendancyClass") or 0
    cls = data.classes()[ch.get("classId", 0)]
    asc_name = cls["ascendancies"][asc - 1]["id"] if asc and asc <= len(cls["ascendancies"]) else None
    b = Build(name or ch.get("name") or character, cls["name"], asc_name, ch.get("level", 90))
    known = [h for h in passives.get("hashes", []) if h in data.nodes()]
    b.tree_nodes = known
    b.masteries = {int(k): int(v) for k, v in (passives.get("mastery_effects") or {}).items()}
    alt = passives.get("alternate_ascendancy")
    if isinstance(alt, int) and alt and alt <= len(data.tree()["bloodlines"]):
        b.bloodline = data.tree()["bloodlines"][alt - 1]["id"]
    for it in items.get("items", []):
        inv = it.get("inventoryId")
        if inv == "Flask":
            b.items[f"Flask {it.get('x', 0) + 1}"] = item_text(it)
        elif inv in SLOT_MAP:
            b.items[SLOT_MAP[inv]] = item_text(it)
    for it in passives.get("items", []):
        x = it.get("x")
        slots = data.tree()["jewelSlots"]
        if isinstance(x, int) and x < len(slots) and slots[x] in data.nodes():
            b.jewels[slots[x]] = item_text(it)
    b.skill_groups = _gem_groups(items.get("items", []))
    b.main_group = max(range(1, len(b.skill_groups) + 1), key=lambda i: len(b.skill_groups[i - 1]["gems"]), default=1)
    b._import_warnings = [] if len(known) == len(passives.get("hashes", [])) else \
        [f"{len(passives.get('hashes', [])) - len(known)} passive ids not in the current tree (cluster jewel nodes)"]
    return b

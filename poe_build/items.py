"""Items in Path of Building's text format (the same text PoB / the game clipboard use)."""
from __future__ import annotations

import re

from . import data

SLOTS = ["Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves", "Boots", "Amulet", "Ring 1", "Ring 2",
         "Belt", "Flask 1", "Flask 2", "Flask 3", "Flask 4", "Flask 5", "Weapon 1 Swap", "Weapon 2 Swap"]

SLOT_TYPES = {
    "Helmet": {"Helmet"}, "Body Armour": {"Body Armour"}, "Gloves": {"Gloves"}, "Boots": {"Boots"},
    "Amulet": {"Amulet"}, "Ring 1": {"Ring"}, "Ring 2": {"Ring"}, "Belt": {"Belt"},
    **{f"Flask {i}": {"Flask"} for i in range(1, 6)},
}
WEAPON_TYPES = {"One Handed Sword", "Thrusting One Handed Sword", "One Handed Axe", "One Handed Mace", "Sceptre",
                "Wand", "Dagger", "Claw", "Two Handed Sword", "Two Handed Axe", "Two Handed Mace", "Staff",
                "Bow", "Fishing Rod", "Rune Dagger", "Warstaff"}


def slot_ok(slot, item_type):
    if slot in SLOT_TYPES:
        return item_type in SLOT_TYPES[slot]
    if slot.startswith("Weapon"):
        return item_type in WEAPON_TYPES or item_type in ("Shield", "Quiver")
    return True


def canon_slot(slot: str) -> str:
    n = data.norm(slot)
    alias = {"weapon": "Weapon 1", "mainhand": "Weapon 1", "main hand": "Weapon 1", "offhand": "Weapon 2",
             "off hand": "Weapon 2", "shield": "Weapon 2", "quiver": "Weapon 2", "helm": "Helmet",
             "body": "Body Armour", "chest": "Body Armour", "armour": "Body Armour", "glove": "Gloves",
             "boot": "Boots", "amulet": "Amulet", "ammy": "Amulet", "ring": "Ring 1", "belt": "Belt",
             "flask": "Flask 1"}
    for s in SLOTS:
        if data.norm(s) == n:
            return s
    if n in alias:
        return alias[n]
    raise KeyError(f"unknown slot '{slot}'. Valid: {SLOTS} (jewels go in jewel sockets by node id)")


def parse_item(text: str) -> dict:
    """Parse PoB/game item text into {rarity,name,base,implicits,explicits,...}."""
    lines = [l.strip() for l in text.strip().replace("\r", "").split("\n")]
    lines = [l for l in lines if l and not l.startswith("--------")]
    it = {"rarity": "NORMAL", "name": "", "base": "", "implicits": [], "explicits": [], "props": {}}
    i = 0
    if lines and lines[0].startswith("Item Class:"):
        i += 1
    if i < len(lines) and lines[i].startswith("Rarity:"):
        it["rarity"] = lines[i].split(":", 1)[1].strip().upper()
        i += 1
    header = []
    while i < len(lines) and not re.match(r"^[A-Za-z][A-Za-z ]*:", lines[i]) and len(header) < 2:
        header.append(lines[i])
        i += 1
    if len(header) == 2 and not data.find_base(header[1]) and it["rarity"] not in ("RARE", "UNIQUE", "RELIC"):
        # second line is not a base: it is the first mod line of a normal/magic item
        header = header[:1]
        i -= 1
    if len(header) >= 2:
        it["name"], it["base"] = header[0], header[1]
        if it["rarity"] == "NORMAL" and data.find_base(header[1]) and not data.find_base(header[0]):
            it["rarity"] = "RARE" if not lines[0].startswith("Rarity:") else it["rarity"]
    elif header:
        it["name"] = it["base"] = header[0]
        b = _base_from_magic(header[0])
        if b:
            it["base"] = b
    implicit_n = None
    mods = []
    for l in lines[i:]:
        m = re.match(r"^(Item Level|Quality|Sockets|LevelReq|Implicits|Variant|Selected Variant|Unique ID|"
                     r"Requires Level|Radius|Limited to|Source|League|Has Alt Variant|Catalyst|Talisman Tier|"
                     r"Crafted|Prefix|Suffix|Cluster Jewel Skill|Cluster Jewel Node Count|Armour|Evasion|"
                     r"Evasion Rating|Energy Shield|Ward|Shaper Item|Elder Item)\s*:\s*(.*)$", l)
        if m:
            k, v = m.group(1), m.group(2)
            if k == "Implicits":
                implicit_n = int(v)
            else:
                it["props"].setdefault(k, v) if k != "Variant" else it["props"].setdefault("Variants", []).append(v)
            continue
        pm = re.match(r"^(\w+BasePercentile|Catalyst Quality|Foulborn|Talisman Tier|Has Variants|Has Alt Variant \w+)\s*:\s*(.*)$", l)
        if pm:
            it["props"][pm.group(1)] = pm.group(2)
            continue
        rm = re.match(r"^Requires Level (\d+)", l)
        if rm:
            it["props"]["Requires Level"] = rm.group(1)
            continue
        if l in ("Corrupted", "Mirrored", "Split", "Fractured Item", "Synthesised Item", "Shaper Item", "Elder Item",
                 "Crusader Item", "Hunter Item", "Redeemer Item", "Warlord Item", "Searing Exarch Item",
                 "Eater of Worlds Item"):
            it.setdefault("flags", []).append(l)
            continue
        mods.append(l)
    if implicit_n is None:
        implicit_n = 0
    it["implicits"] = mods[:implicit_n]
    it["explicits"] = mods[implicit_n:]
    return it


def _base_from_magic(name):
    best = None
    for b in data.bases():
        if b in name and (best is None or len(b) > len(best)):
            best = b
    return best


def strip_tags(line: str) -> str:
    return re.sub(r"\{[^}]*\}", "", line)


def active_mod_lines(it: dict) -> list[str]:
    """Mod lines that apply, honouring {variant:x} tags against Selected Variant."""
    sel = it["props"].get("Selected Variant")
    sel = int(sel) if sel and str(sel).isdigit() else None
    out = []
    for l in it["implicits"] + it["explicits"]:
        vm = re.search(r"\{variant:([\d,]+)\}", l)
        if vm and sel is not None and sel not in [int(x) for x in vm.group(1).split(",")]:
            continue
        s = strip_tags(l).strip()
        if s:
            out.append(s)
    return out


def unique_text(name: str, variant: int | None = None) -> str:
    u = data.find_unique(name)
    if not u:
        raise KeyError(f"unique '{name}' not found (try search_uniques)")
    raw = u["raw"]
    lines = raw.split("\n")
    if u["variants"]:
        v = variant or u["currentVariant"]
        # put Selected Variant after the variant list
        last_var = max(i for i, l in enumerate(lines) if l.startswith("Variant:"))
        lines.insert(last_var + 1, f"Selected Variant: {v}")
    return "Rarity: UNIQUE\n" + "\n".join(lines)


def rare_text(base: str, mods: list[str], name: str = "Claude's Creation", implicits: list[str] | None = None,
              rarity: str = "RARE", item_level: int = 86, quality: int | None = None, sockets: str | None = None,
              influences: list[str] | None = None, corrupted: bool = False) -> str:
    found = data.find_base(base)
    if not found:
        raise KeyError(f"base '{base}' not found (try search_bases)")
    bname, b = found
    if implicits is None:
        implicits = [b["implicit"]] if b.get("implicit") else []
        implicits = [x for imp in implicits for x in imp.split("\n")]
    lines = [f"Rarity: {rarity.upper()}"]
    if rarity.upper() in ("RARE", "UNIQUE"):
        lines.append(name)
    lines.append(bname)
    for inf in influences or []:
        lines.append(f"{inf} Item")
    lines.append(f"Item Level: {item_level}")
    if quality is None and (b.get("armour") or b.get("weapon")):
        quality = 20
    if quality:
        lines.append(f"Quality: {quality}")
    if sockets:
        lines.append(f"Sockets: {sockets}")
    lines.append(f"Implicits: {len(implicits)}")
    lines += implicits
    lines += mods
    if corrupted:
        lines.append("Corrupted")
    return "\n".join(lines)


def item_type(text: str) -> str | None:
    it = parse_item(text)
    fb = data.find_base(it["base"])
    return fb[1]["type"] if fb else None


# ---------------------------------------------------------------- mod pools
def mod_pool(base: str, kind="explicit", affix=None, query=None, limit=60):
    """Mods that can roll on a base (by PoE spawn-weight tag rules)."""
    found = data.find_base(base)
    if not found:
        raise KeyError(f"base '{base}' not found")
    bname, b = found
    tags = set(b.get("tags", []))
    pool = data.mods().get(kind, [])
    out = []
    words = data.norm(query).split() if query else []
    for m in pool:
        if kind == "crafted":
            if b["type"] not in m.get("itemTypes", []):
                continue
        else:
            w = m.get("weights")
            if not w:
                continue
            # first matching tag in original weight order decides (dict keeps order)
            weight = 0
            for t, v in w.items():
                if t in tags:
                    weight = v
                    break
            if weight <= 0:
                continue
        if affix and m.get("type", "").lower() != affix.lower():
            continue
        if words and not all(x in data.norm(" ".join(m["text"])) for x in words):
            continue
        out.append({k: m[k] for k in ("id", "type", "affix", "level", "group", "text") if k in m})
    out.sort(key=lambda m: (m.get("type", ""), m.get("group", ""), -(m.get("level") or 0)))
    return out[:limit]

"""One function per tool. Shared by the CLI, the MCP server and the web app.

Every function takes plain JSON-able arguments and returns a JSON-able dict.
TOOLS describes each one (name, description, parameters) for MCP/CLI.
"""
from __future__ import annotations

import re
import urllib.request

from . import data, items as itemsmod, skills as skillsmod, stats as statsmod
from .build import Build, list_builds as _list_builds, delete_build as _delete_build
from .tree import TreeSpec, find_nodes, resolve_node, points_available

TOOLS = []


def tool(desc, **params):
    """params: name=(type, description, required?)"""
    def deco(fn):
        TOOLS.append({"name": fn.__name__, "fn": fn, "description": desc, "params": params})
        return fn
    return deco


def _node_brief(nid, full=False):
    n = data.nodes()[nid]
    e = {"id": nid, "name": n["name"], "type": n["type"], "stats": n.get("stats", [])}
    if n.get("ascendancy"):
        e["ascendancy"] = n["ascendancy"]
    if n.get("masteryEffects"):
        e["masteryEffects"] = n["masteryEffects"]
    if full:
        for k in ("reminder", "anointRecipe", "flavour", "adj", "x", "y", "group"):
            if n.get(k) is not None:
                e[k] = n[k]
    return e


# ================================================================== reference data
@tool("Overview of the game data: tree version, classes, ascendancies, bloodlines and data counts.")
def info():
    t = data.tree()
    return {
        "treeVersion": t["version"], "source": data.meta()["source"],
        "classes": [{"name": c["name"], "classId": c["classId"], "base_attributes": [c["str"], c["dex"], c["int"]],
                     "ascendancies": [a["id"] for a in c["ascendancies"]]} for c in t["classes"]],
        "bloodlines": [b["name"] + f" ({b['id']})" for b in t["bloodlines"]],
        "counts": {"passive_nodes": len(t["nodes"]), "gems": len(data.gems()), "uniques": len(data.uniques()),
                   "bases": len(data.bases()), "mods": {k: len(v) for k, v in data.mods().items()}},
        "slots": itemsmod.SLOTS,
        "bandits": ["None (kill all, +1 passive point)", "Alira", "Kraityn", "Oak"],
    }


@tool("Search skill & support gems by name, tag or description words.",
      query=("string", "words to match, e.g. 'fire projectile' or 'spell echo'", True),
      support=("boolean", "true = only supports, false = only active skills", False),
      limit=("integer", "max results (default 25)", False))
def search_gems(query, support=None, limit=25):
    words = data.norm(query).split()
    out = []
    for g in data.gems().values():
        if support is not None and g["support"] != bool(support):
            continue
        hay = data.norm(" ".join([g["name"], g.get("tags", ""), g.get("description", "")]))
        if all(w in hay for w in words):
            out.append({"name": g["name"], "support": g["support"], "tags": g.get("tags"),
                        "description": (g.get("description") or "")[:200]})
    out.sort(key=lambda g: (not data.norm(g["name"]).startswith(words[0] if words else ""), g["name"]))
    return {"count": len(out), "results": out[: int(limit)]}


@tool("Full data for one gem: description, tags, skill types, support restrictions, quality stats, per-level stats/costs.",
      name=("string", "gem name, e.g. 'Fireball' or 'Spell Echo Support'", True))
def gem_info(name):
    return skillsmod.resolve_gem(name)


@tool("List support gems that can support a given active skill (checks PoB skill-type rules).",
      active=("string", "active skill gem name", True),
      query=("string", "optional words to filter supports", False))
def compatible_supports(active, query=None):
    return {"active": active, "supports": skillsmod.compatible_supports(active, query, limit=400)}


@tool("Search unique items by name, base, slot category or mod text.",
      query=("string", "words to match (name, base or any mod text), e.g. 'life leech' or 'Headhunter'", True),
      category=("string", "optional: amulet, axe, belt, body, boots, bow, claw, dagger, flask, gloves, helmet, jewel, mace, quiver, ring, shield, staff, sword, wand, ...", False),
      limit=("integer", "max results (default 20)", False))
def search_uniques(query, category=None, limit=20):
    words = data.norm(query).split()
    out = []
    for u in data.uniques():
        if category and u["category"] != category.lower():
            continue
        hay = data.norm(" ".join([u["name"], u["base"]] + u["implicits"] + u["explicits"]))
        if all(w in hay for w in words):
            out.append({"name": u["name"], "base": u["base"], "category": u["category"],
                        "mods": u["implicits"] + u["explicits"]})
    out.sort(key=lambda u: (data.norm(u["name"]) != data.norm(query), u["name"]))
    return {"count": len(out), "results": out[: int(limit)]}


@tool("Full data for one unique item (all variants, mods, source/league, raw PoB text).",
      name=("string", "unique name", True))
def unique_info(name):
    u = data.find_unique(name)
    if not u:
        raise KeyError(f"unique '{name}' not found")
    fb = data.find_base(u["base"])
    return {**u, "baseData": fb[1] if fb else None}


@tool("Search item bases (white items) by name/type/tag, with base stats and requirements.",
      query=("string", "words, e.g. 'circlet' or 'energy shield body' or 'claw'", True),
      type=("string", "optional item type filter, e.g. 'Helmet', 'Body Armour', 'Ring', 'Bow'", False),
      limit=("integer", "max results (default 30)", False))
def search_bases(query, type=None, limit=30):
    words = data.norm(query).split()
    out = []
    for name, b in data.bases().items():
        if b.get("hidden"):
            continue
        if type and data.norm(b.get("type")) != data.norm(type):
            continue
        hay = data.norm(" ".join([name, b.get("type") or "", b.get("subType") or "", " ".join(b.get("tags", [])),
                                  b.get("implicit") or ""]))
        if all(w in hay for w in words):
            out.append({"name": name, **{k: b[k] for k in ("type", "subType", "implicit", "req", "armour", "weapon")
                                         if b.get(k)}})
    out.sort(key=lambda x: -(x.get("req", {}).get("level", 0)))
    return {"count": len(out), "results": out[: int(limit)]}


@tool("Mods that can roll on an item base (prefix/suffix tiers with ilvl), for crafting realistic rares.",
      base=("string", "base item name, e.g. 'Hubris Circlet'", True),
      query=("string", "optional words to filter mod text, e.g. 'life' or 'fire resistance'", False),
      affix=("string", "optional 'Prefix' or 'Suffix'", False),
      kind=("string", "pool: explicit (default), crafted (bench), corrupted, veiled, eldritch, delve, synthesis, jewel, jewelAbyss, flask ...", False),
      limit=("integer", "max results (default 60)", False))
def search_mods(base, query=None, affix=None, kind="explicit", limit=60):
    r = itemsmod.mod_pool(base, kind or "explicit", affix, query, int(limit))
    return {"base": base, "kind": kind, "count": len(r), "mods": r}


@tool("Search passive tree nodes (notables, keystones, masteries, jewel sockets, ascendancy nodes) by name or stat text.",
      query=("string", "words, e.g. 'life regeneration' or 'Elemental Equilibrium'", True),
      kind=("string", "optional: normal, notable, keystone, mastery, jewel", False),
      ascendancy=("string", "optional ascendancy id to search only its nodes, e.g. 'Elementalist'", False),
      limit=("integer", "max results (default 30)", False))
def search_passives(query, kind=None, ascendancy=None, limit=30):
    ids = find_nodes(query, kind, ascendancy, int(limit))
    return {"count": len(ids), "results": [_node_brief(i) for i in ids]}


@tool("Details of one passive node by id or exact name (stats, mastery options, neighbours, anoint recipe).",
      node=("string", "node id or name", True))
def passive_info(node):
    return _node_brief(resolve_node(node), full=True)


@tool("Look up other game data: essences, enchantments, clusterJewels, pantheons, minions, spectres, bosses, tattoos.",
      kind=("string", "one of: essences, enchantments, clusterJewels, pantheons, minions, spectres, bosses, bossSkills, tattoos", True),
      query=("string", "optional words to filter", False),
      limit=("integer", "max results (default 30)", False))
def lookup(kind, query=None, limit=30):
    m = data.misc()
    if kind not in m:
        raise KeyError(f"kind must be one of {list(m)}")
    src = m[kind]
    words = data.norm(query).split() if query else []
    entries = list(src.items()) if isinstance(src, dict) else list(enumerate(src))
    if kind == "clusterJewels" and isinstance(src, dict):
        entries = []
        for size, j in src.get("jewels", {}).items():
            for k, s in j.get("skills", {}).items():
                entries.append((f"{size}: {k}", s))
    out = []
    for k, v in entries:
        hay = data.norm(str(k) + " " + data._flat(v))
        if all(w in hay for w in words):
            out.append({"key": k, "value": v})
            if len(out) >= int(limit):
                break
    return {"kind": kind, "count": len(out), "results": out}


# ================================================================== builds
def _load(name):
    return Build.load(name)


def _brief(b):
    s = b.summary()
    return {k: s[k] for k in ("name", "class", "ascendancy", "level", "points", "stats", "problems")}


@tool("List saved builds.")
def list_builds():
    return {"builds": _list_builds()}


@tool("Create a new build (overwrites if it exists).",
      name=("string", "build name", True),
      class_name=("string", "class (Marauder, Ranger, Witch, Duelist, Templar, Shadow, Scion) or an ascendancy name", True),
      ascendancy=("string", "optional ascendancy, e.g. 'Elementalist'", False),
      level=("integer", "character level (default 90)", False))
def new_build(name, class_name, ascendancy=None, level=90):
    b = Build(name, class_name, ascendancy, level)
    b.save()
    return {"created": b.name, "file": b.path(), **_brief(b)}


@tool("Show a build: tree summary (keystones, notables, masteries), items, skill groups, quick stats and problems.",
      name=("string", "build name", True),
      full=("boolean", "include full item text and summed stat totals", False))
def show_build(name, full=False):
    return _load(name).summary(full=bool(full))


@tool("Quick stat estimate for a build: attributes, life/mana/ES, armour/evasion, resists (after -60%), point usage, every stat line summed.",
      name=("string", "build name", True))
def build_stats(name):
    return statsmod.compute(_load(name))


@tool("Delete a saved build.", name=("string", "build name", True))
def delete_build(name):
    return {"deleted": _delete_build(name)}


@tool("Change build settings.",
      name=("string", "build name", True),
      level=("integer", "character level", False),
      class_name=("string", "change class (resets tree if different)", False),
      ascendancy=("string", "change ascendancy", False),
      bloodline=("string", "secondary ascendancy / bloodline name or 'None'", False),
      bandit=("string", "None (kill all), Alira, Kraityn or Oak", False),
      pantheon_major=("string", "e.g. TheBrineKing, Arakaali, Solaris, Lunaris", False),
      pantheon_minor=("string", "e.g. Gruthkul, Yugul, Abberath, Tukohama, Garukhan, Ralakesh, Ryslatha, Shakari", False),
      notes=("string", "build notes / guide text (replaces existing)", False),
      rename=("string", "new name", False))
def set_build(name, level=None, class_name=None, ascendancy=None, bloodline=None, bandit=None,
              pantheon_major=None, pantheon_minor=None, notes=None, rename=None):
    b = _load(name)
    if level is not None:
        b.level = int(level)
    if class_name or ascendancy:
        b.set_class(class_name or b.class_name, ascendancy)
    if bloodline is not None:
        b.set_bloodline(bloodline)
    if bandit is not None:
        if bandit not in ("None", "Alira", "Kraityn", "Oak"):
            raise ValueError("bandit must be None, Alira, Kraityn or Oak")
        b.bandit = bandit
    if pantheon_major is not None:
        b.pantheon_major = pantheon_major
    if pantheon_minor is not None:
        b.pantheon_minor = pantheon_minor
    if notes is not None:
        b.notes = notes
    if rename:
        _delete_build(b.name)
        b.name = rename
    b.save()
    return _brief(b)


@tool("Allocate passive nodes. Each target is pathed automatically by the shortest route from your current tree. "
      "For masteries also give mastery_effect (see passive_info for effect ids).",
      name=("string", "build name", True),
      nodes=("array", "node ids or exact names, e.g. ['Heart of Flame', 'Elemental Overload', 12345]", True),
      mastery_effect=("integer", "effect id when allocating a single mastery", False))
def allocate(name, nodes, mastery_effect=None):
    b = _load(name)
    if isinstance(nodes, (str, int)):
        nodes = [nodes]
    added = b.allocate(list(nodes), mastery_effect)
    b.save()
    return {"added": [data.nodes()[n]["name"] + f" ({n})" for n in added], "points": b.spec().counts(),
            "points_available": points_available(b.level, b.bandit)}


@tool("Preview the path (and point cost) to a node without allocating it.",
      name=("string", "build name", True), node=("string", "node id or name", True))
def path_to(name, node):
    b = _load(name)
    p = b.spec().path_to(resolve_node(node, b.ascendancy))
    return {"cost": len(p), "path": [_node_brief(n) for n in p]}


@tool("Remove passive nodes (anything left disconnected is removed too).",
      name=("string", "build name", True), nodes=("array", "node ids or names", True))
def deallocate(name, nodes):
    b = _load(name)
    if isinstance(nodes, (str, int)):
        nodes = [nodes]
    removed = b.deallocate(list(nodes))
    b.save()
    return {"removed": [data.nodes()[n]["name"] + f" ({n})" for n in removed], "points": b.spec().counts()}


@tool("Clear the whole passive tree (keeps class/ascendancy).", name=("string", "build name", True))
def reset_tree(name):
    b = _load(name)
    b.reset_tree()
    b.save()
    return {"ok": True}


@tool("Equip a unique item by name.",
      name=("string", "build name", True),
      slot=("string", "Weapon 1, Weapon 2, Helmet, Body Armour, Gloves, Boots, Amulet, Ring 1, Ring 2, Belt, Flask 1-5, or a jewel socket node id", True),
      unique=("string", "unique item name", True),
      variant=("integer", "optional variant number (default = current version)", False))
def equip_unique(name, slot, unique, variant=None):
    b = _load(name)
    where = b.equip(slot, itemsmod.unique_text(unique, variant))
    b.save()
    return {"equipped": unique, "slot": where, **_brief(b)}


@tool("Create and equip a rare/magic/normal item from a base and a list of mod lines. "
      "Use search_mods to pick mods that can really roll. Ranges like '+(80-89) to maximum Life' or fixed numbers both work.",
      name=("string", "build name", True),
      slot=("string", "slot name or jewel socket node id", True),
      base=("string", "base item, e.g. 'Hubris Circlet', 'Cobalt Jewel'", True),
      mods=("array", "explicit mod lines", True),
      implicits=("array", "optional implicit lines (default: base implicit)", False),
      item_name=("string", "optional rare name", False),
      rarity=("string", "RARE (default), MAGIC or NORMAL", False),
      quality=("integer", "optional quality", False),
      sockets=("string", "optional sockets, e.g. 'B-B-B-B-B-B'", False),
      influences=("array", "optional: Shaper, Elder, Crusader, Hunter, Redeemer, Warlord", False),
      corrupted=("boolean", "optional", False))
def equip_rare(name, slot, base, mods, implicits=None, item_name=None, rarity="RARE", quality=None, sockets=None,
               influences=None, corrupted=False):
    b = _load(name)
    text = itemsmod.rare_text(base, list(mods), item_name or "Claude's Creation", implicits, rarity or "RARE",
                              quality=quality, sockets=sockets, influences=influences, corrupted=bool(corrupted))
    where = b.equip(slot, text)
    b.save()
    return {"slot": where, "item": text, **_brief(b)}


@tool("Equip an item from raw item text (PoB format or Ctrl+C from the game).",
      name=("string", "build name", True), slot=("string", "slot or jewel socket node id", True),
      text=("string", "item text", True))
def equip_text(name, slot, text):
    b = _load(name)
    where = b.equip(slot, text)
    b.save()
    return {"slot": where, **_brief(b)}


@tool("Remove the item in a slot.", name=("string", "build name", True), slot=("string", "slot or socket id", True))
def unequip(name, slot):
    b = _load(name)
    ok = b.unequip(slot)
    b.save()
    return {"removed": ok}


@tool("Add a skill/socket group. Supports are checked against the active skill's types.",
      name=("string", "build name", True),
      gems=("array", "gem names, or objects {name, level, quality}; e.g. ['Fireball', 'Spell Echo', 'Elemental Focus']", True),
      slot=("string", "item slot the gems are socketed in, e.g. 'Body Armour'", False),
      label=("string", "optional label", False),
      main=("boolean", "make this the main skill", False))
def add_skill_group(name, gems, slot=None, label=None, main=False):
    b = _load(name)
    idx, warns = b.add_group(list(gems), slot, label, bool(main))
    b.save()
    return {"group": idx, "warnings": warns, "main": b.main_group}


@tool("Remove a skill group by its 1-based index.", name=("string", "build name", True),
      index=("integer", "group index", True))
def remove_skill_group(name, index):
    b = _load(name)
    b.remove_group(int(index))
    b.save()
    return {"ok": True, "groups": len(b.skill_groups)}


@tool("Set which skill group is the main skill.", name=("string", "build name", True),
      index=("integer", "group index", True))
def set_main_skill(name, index):
    b = _load(name)
    b.main_group = int(index)
    b.save()
    return {"main": b.main_group}


@tool("Export a build as a Path of Building import code (paste into PoB: Import/Export Build > Import from code) "
      "to get the exact DPS/EHP calculations.", name=("string", "build name", True))
def export_pob(name):
    b = _load(name)
    return {"code": b.export_code(), "how": "Path of Building > Import/Export Build > paste code > Import"}


@tool("Import a build from a PoB code or a pobb.in / pastebin link, and save it.",
      code=("string", "PoB import code or URL", True), name=("string", "name to save as", True))
def import_pob(code, name):
    code = code.strip()
    if code.startswith("http"):
        url = code
        m = re.match(r"https?://pobb\.in/([\w-]+)", code)
        if m:
            url = f"https://pobb.in/{m.group(1)}/raw"
        m = re.match(r"https?://pastebin\.com/(?:raw/)?(\w+)", code)
        if m:
            url = f"https://pastebin.com/raw/{m.group(1)}"
        req = urllib.request.Request(url, headers={"User-Agent": "poe-build-tool"})
        with urllib.request.urlopen(req, timeout=20) as r:
            code = r.read().decode().strip()
    b = Build.from_pob_code(code, name)
    b.save()
    return {"imported": b.name, "warnings": getattr(b, "_import_warnings", []), **b.summary()}


@tool("Generate the item text for a unique (without equipping) - useful to read exact mods.",
      unique=("string", "unique name", True), variant=("integer", "optional variant", False))
def unique_text(unique, variant=None):
    return {"text": itemsmod.unique_text(unique, variant)}


def call(name, args):
    for t in TOOLS:
        if t["name"] == name:
            return t["fn"](**(args or {}))
    raise KeyError(f"unknown tool {name}")

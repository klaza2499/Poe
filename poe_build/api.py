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


@tool("Full stats for a build from the calc engine: main-skill DPS (hit, DoT, ignite/bleed/poison), crit, speed, "
      "life/mana/ES/ward, armour/evasion, resists, block, suppression, max hit taken, reservation, plus 'problems' "
      "(uncapped resists, attribute/level requirements, points) and every stat line summed.",
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


# ================================================================== calc-powered tools
def _metrics(b):
    from . import calc
    r = calc.calculate(b)
    o, d = r["offence"], r["defence"]
    m = {"total_dps": o.get("total_dps", 0), "hit_dps": o.get("hit_dps", 0), "dot_dps": o.get("dot_dps", 0),
         "ignite_dps": (o.get("ignite") or {}).get("dps", 0), "bleed_dps": (o.get("bleed") or {}).get("dps", 0),
         "poison_dps": (o.get("poison") or {}).get("dps", 0), "average_hit": o.get("average_hit", 0),
         "speed": o.get("uses_per_second", 0), "crit_chance": o.get("crit_chance", 0),
         "life": d["life"], "energy_shield": d["energy_shield"], "mana": d["mana"],
         "mana_unreserved": d["mana_unreserved"], "armour": d["armour"], "evasion": d["evasion"],
         "block": d["block"], "spell_suppression": d["spell_suppression"],
         "str": r["attributes"]["str"], "dex": r["attributes"]["dex"], "int": r["attributes"]["int"]}
    for el, v in d["resistances"].items():
        m[f"{el.lower()}_res"] = v["effective"]
    for k, v in d["max_hit_taken"].items():
        m[f"max_hit_{k.lower()}"] = v
    m["ehp"] = d["life"] + d["energy_shield"]
    return m


def _delta(before, after):
    out = {}
    for k in before:
        a, b2 = before.get(k) or 0, after.get(k) or 0
        if isinstance(a, (int, float)) and isinstance(b2, (int, float)) and abs(b2 - a) > 1e-6:
            out[k] = {"before": round(a, 1), "after": round(b2, 1), "change": round(b2 - a, 1),
                      "percent": round((b2 - a) / a * 100, 1) if a else None}
    return out


def _clone(b, name=None):
    c = Build.from_dict(b.to_dict())
    if name:
        c.name = name
    return c


@tool("Calculate a build's DPS and defences in detail (same engine as build_stats, without requirement checks).",
      name=("string", "build name", True))
def calc_build(name):
    from . import calc
    return calc.calculate(_load(name))


@tool("Try changes WITHOUT saving and see the difference in DPS, life, ES, resists, max hit etc. "
      "Combine any of: allocate/deallocate nodes, equip uniques or item text, change gem levels, config, level.",
      name=("string", "build name", True),
      allocate=("array", "nodes to add (ids or names)", False),
      deallocate=("array", "nodes to remove", False),
      equip_unique=("object", "{slot: unique name}", False),
      equip_text=("object", "{slot: item text}", False),
      add_gems=("object", "{group index: [gem names]} supports/gems to add to existing groups", False),
      config=("object", "PoB config values to set, e.g. {\"usePowerCharges\": true}", False),
      level=("integer", "character level", False))
def what_if(name, allocate=None, deallocate=None, equip_unique=None, equip_text=None, add_gems=None, config=None,
            level=None):
    b = _load(name)
    before = _metrics(b)
    c = _clone(b)
    added = c.allocate(list(allocate)) if allocate else []
    if deallocate:
        c.deallocate(list(deallocate))
    for slot, u in (equip_unique or {}).items():
        c.equip(slot, itemsmod.unique_text(u))
    for slot, t in (equip_text or {}).items():
        c.equip(slot, t)
    for gi, gems in (add_gems or {}).items():
        grp = c.skill_groups[int(gi) - 1]
        for g in gems:
            gd = skillsmod.resolve_gem(g)
            grp["gems"].append({"name": gd["name"], "level": 20, "quality": 20, "enabled": True})
    if config:
        c.config.update(config)
    if level:
        c.level = int(level)
    after = _metrics(c)
    return {"points_spent": len([n for n in added if data.nodes()[n]["type"] not in ("classStart", "ascendancyStart")]),
            "changes": _delta(before, after)}


_OBJECTIVES = {"dps": "total_dps", "life": "life", "es": "energy_shield", "ehp": "ehp", "armour": "armour",
               "evasion": "evasion", "mana": "mana", "max_hit": "max_hit_physical", "speed": "speed",
               "crit": "crit_chance"}


@tool("Rank reachable notables/keystones by how much they improve an objective per passive point "
      "(like PoB's node power). Good for finishing a tree.",
      name=("string", "build name", True),
      objective=("string", "dps (default), life, es, ehp, armour, evasion, mana, max_hit, speed, crit", False),
      max_cost=("integer", "max points to reach a node (default 6)", False),
      kind=("string", "notable (default), keystone, or any", False),
      limit=("integer", "number of results (default 10)", False))
def suggest_passives(name, objective="dps", max_cost=6, kind="notable", limit=10):
    b = _load(name)
    key = _OBJECTIVES.get((objective or "dps").lower(), "total_dps")
    base = _metrics(b)
    sp = b.spec()
    cands = []
    kinds = {"notable", "keystone"} if kind in (None, "any") else {kind}
    for nid, n in data.nodes().items():
        if n["type"] not in kinds or nid in sp.alloc or n.get("ascendancy") or n.get("isProxy"):
            continue
        try:
            p = sp.path_to(nid)
        except Exception:
            continue
        if 0 < len(p) <= int(max_cost):
            cands.append((nid, p))
    res = []
    for nid, p in cands:
        c = _clone(b)
        c.tree_nodes = sorted(set(c.tree_nodes) | set(p))
        m = _metrics(c)
        gain = (m.get(key) or 0) - (base.get(key) or 0)
        res.append({"id": nid, "name": data.nodes()[nid]["name"], "cost": len(p), "gain": round(gain, 1),
                    "gain_percent": round(gain / base[key] * 100, 2) if base.get(key) else None,
                    "per_point": round(gain / len(p), 1), "stats": data.nodes()[nid].get("stats", []),
                    "side_effects": {k: v for k, v in _delta(base, m).items() if k in ("life", "total_dps", "energy_shield")
                                     and k != key}})
    res.sort(key=lambda r: -r["per_point"])
    return {"objective": key, "current": base.get(key), "candidates_checked": len(cands), "best": res[: int(limit)]}


@tool("Compare two saved builds side by side (DPS, defences, resists, attributes).",
      a=("string", "first build", True), b=("string", "second build", True))
def compare_builds(a, b):
    ma, mb = _metrics(_load(a)), _metrics(_load(b))
    return {"a": a, "b": b, "metrics": {k: {"a": ma[k], "b": mb[k]} for k in ma}, "difference_b_minus_a": _delta(ma, mb)}


@tool("Copy a build under a new name (to try variants).", name=("string", "build name", True),
      new_name=("string", "name for the copy", True))
def clone_build(name, new_name):
    c = _clone(_load(name), new_name)
    c.save()
    return {"created": new_name}


# ---------------------------------------------------------------- gems
@tool("Change one gem in a skill group: level, quality, enabled, or swap it for another gem.",
      name=("string", "build name", True), group=("integer", "group index (1-based)", True),
      gem=("string", "gem name currently in the group", True),
      level=("integer", "new level", False), quality=("integer", "new quality", False),
      enabled=("boolean", "enable/disable", False), replace_with=("string", "new gem name", False))
def set_gem(name, group, gem, level=None, quality=None, enabled=None, replace_with=None):
    b = _load(name)
    grp = b.skill_groups[int(group) - 1]
    target = next((g for g in grp["gems"] if data.norm(g["name"]) == data.norm(skillsmod.resolve_gem(gem)["name"])), None)
    if not target:
        raise KeyError(f"{gem} not in group {group}: {[g['name'] for g in grp['gems']]}")
    if replace_with:
        target["name"] = skillsmod.resolve_gem(replace_with)["name"]
    if level is not None:
        target["level"] = int(level)
    if quality is not None:
        target["quality"] = int(quality)
    if enabled is not None:
        target["enabled"] = bool(enabled)
    b.save()
    return {"group": group, "gems": grp["gems"], "warnings": skillsmod.check_group(grp)}


@tool("Add gems to an existing skill group.", name=("string", "build name", True),
      group=("integer", "group index", True), gems=("array", "gem names", True))
def add_gems(name, group, gems):
    b = _load(name)
    grp = b.skill_groups[int(group) - 1]
    for g in gems:
        gd = skillsmod.resolve_gem(g)
        grp["gems"].append({"name": gd["name"], "level": 20 if gd["maxLevel"] >= 20 else gd["maxLevel"],
                            "quality": 20, "enabled": True})
    b.save()
    return {"gems": [g["name"] for g in grp["gems"]], "warnings": skillsmod.check_group(grp)}


@tool("Remove a gem from a skill group.", name=("string", "build name", True),
      group=("integer", "group index", True), gem=("string", "gem name", True))
def remove_gem(name, group, gem):
    b = _load(name)
    grp = b.skill_groups[int(group) - 1]
    n = data.norm(skillsmod.resolve_gem(gem)["name"])
    grp["gems"] = [g for g in grp["gems"] if data.norm(g["name"]) != n]
    b.save()
    return {"gems": [g["name"] for g in grp["gems"]]}


@tool("Edit a skill group's slot, label or enabled state.", name=("string", "build name", True),
      group=("integer", "group index", True), slot=("string", "item slot", False),
      label=("string", "label", False), enabled=("boolean", "enabled", False))
def edit_skill_group(name, group, slot=None, label=None, enabled=None):
    b = _load(name)
    grp = b.skill_groups[int(group) - 1]
    if slot is not None:
        grp["slot"] = itemsmod.canon_slot(slot) if slot else None
    if label is not None:
        grp["label"] = label
    if enabled is not None:
        grp["enabled"] = bool(enabled)
    b.save()
    return {"group": grp}


# ---------------------------------------------------------------- config
@tool("Search Path of Building's Config options (enemy boss type/resists, charges, conditions like "
      "'on full life', 'enemy is shocked', flasks, buffs ...). Returns var names to use with set_config.",
      query=("string", "words, e.g. 'power charges', 'shocked', 'boss', 'full life'", True),
      name=("string", "optional build name to show current values", False),
      limit=("integer", "max results (default 25)", False))
def search_config(query, name=None, limit=25):
    from . import calc
    cur = _load(name).config if name else {}
    words = data.norm(query).split()
    out = []
    for o in calc.config_options():
        hay = data.norm(" ".join([o.get("var", ""), o.get("label", ""), o.get("section") or "", o.get("tooltip", "")]))
        if all(w in hay for w in words):
            e = {"var": o["var"], "type": o["type"], "label": o["label"], "section": o.get("section")}
            if o.get("list"):
                e["choices"] = [x["val"] for x in o["list"]]
            if name:
                e["current"] = cur.get(o["var"])
            out.append(e)
    return {"count": len(out), "results": out[: int(limit)]}


@tool("Set PoB Config values on a build (use search_config for var names). "
      "check -> true/false, count -> number, list -> one of choices. null removes a value.",
      name=("string", "build name", True),
      values=("object", "e.g. {\"enemyIsBoss\": \"Uber\", \"usePowerCharges\": true, \"conditionEnemyShocked\": true}", True))
def set_config(name, values):
    from . import calc
    valid = {o["var"]: o for o in calc.config_options()}
    b = _load(name)
    for k, v in (values or {}).items():
        if k not in valid:
            raise KeyError(f"unknown config var '{k}' (use search_config)")
        o = valid[k]
        if v is None:
            b.config.pop(k, None)
            continue
        if o.get("list") and str(v) not in [x["val"] for x in o["list"]]:
            raise ValueError(f"{k} must be one of {[x['val'] for x in o['list']]}")
        b.config[k] = v
    b.save()
    return {"config": b.config, "dps": _metrics(b)["total_dps"]}


@tool("Show a build's current config values.", name=("string", "build name", True))
def get_config(name):
    from . import calc
    b = _load(name)
    labels = {o["var"]: o["label"] for o in calc.config_options()}
    return {"config": {k: {"value": v, "label": labels.get(k)} for k, v in b.config.items()},
            "defaults": "enemyIsBoss=Pinnacle (50% ele / 30% chaos res, level 84) unless set; charges off unless usePowerCharges etc."}


# ---------------------------------------------------------------- items
@tool("Inspect an item: how PoB parses each mod line (modifier names/values), unparsed lines, and local "
      "weapon DPS / armour values.",
      text=("string", "item text (or leave empty and give unique)", False),
      unique=("string", "unique name instead of text", False), slot=("string", "slot (for socketed-gem mods)", False))
def inspect_item(text=None, unique=None, slot=None):
    from . import calc
    if unique:
        text = itemsmod.unique_text(unique)
    if not text:
        raise ValueError("give text or unique")
    it, base, mods, weapon, armour, unparsed = calc.parse_item_mods(text, slot or "Weapon 1")
    out = {"name": it["name"], "base": it["base"], "type": base.get("type"), "mods": [
        {k: m.get(k) for k in ("name", "type", "value", "tags") if m.get(k) not in (None, [])} for m in mods],
        "unparsed": unparsed}
    if weapon:
        w = dict(weapon)
        w["dps"] = {d: round((weapon.get(d + "Min", 0) + weapon.get(d + "Max", 0)) / 2 * weapon["AttackRate"], 1)
                    for d in ("Physical", "Fire", "Cold", "Lightning", "Chaos") if weapon.get(d + "Min")}
        out["weapon"] = w
    if armour:
        out["armour"] = armour
    return out


# ---------------------------------------------------------------- character import
@tool("Import a character from pathofexile.com (profile must be public) and save it as a build.",
      account=("string", "account name incl. #1234 discriminator if any", True),
      character=("string", "character name", True), name=("string", "build name (default: character name)", False),
      realm=("string", "pc (default), xbox, sony", False))
def import_character(account, character, name=None, realm="pc"):
    from . import charimport
    b = charimport.import_character(account, character, name, realm or "pc")
    b.save()
    return {"imported": b.name, "warnings": getattr(b, "_import_warnings", []), **b.summary()}


# ---------------------------------------------------------------- loadouts
@tool("Save the build's current tree, items, gems and config as a named loadout (like PoB's tree/item/skill sets), "
      "e.g. 'Leveling', 'Mapping', 'Bossing'.",
      name=("string", "build name", True), loadout=("string", "loadout name", True))
def save_loadout(name, loadout):
    b = _load(name)
    b.save_loadout(loadout)
    b.save()
    return {"saved": loadout, "loadouts": list(b.loadouts)}


@tool("Switch the build to a saved loadout (edits to the current one are kept in it).",
      name=("string", "build name", True), loadout=("string", "loadout name", True))
def switch_loadout(name, loadout):
    b = _load(name)
    b.switch_loadout(loadout)
    b.save()
    return {"active": loadout, **_brief(b)}


@tool("List a build's loadouts with their key numbers.", name=("string", "build name", True))
def list_loadouts(name):
    b = _load(name)
    out = {}
    for ln in b.loadouts:
        c = _clone(b)
        c.switch_loadout(ln)
        m = _metrics(c)
        out[ln] = {"level": c.level, "total_dps": m["total_dps"], "life": m["life"], "energy_shield": m["energy_shield"]}
    return {"active": b.active_loadout, "loadouts": out}


def call(name, args):
    for t in TOOLS:
        if t["name"] == name:
            return t["fn"](**(args or {}))
    raise KeyError(f"unknown tool {name}")

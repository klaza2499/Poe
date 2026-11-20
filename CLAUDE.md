# Notes for Claude

This repo has two tools:
- `poe_act_helper/`: an Act 1–10 overlay (Tkinter).
- `poe_build/`: a Path of Exile 1 build planner (like Path of Building) **made for you to use**.

## Making builds with poe_build

The MCP server `poe-build` (in `.mcp.json`) exposes every tool. If MCP isn't available, use the CLI. It runs the same functions and prints JSON:

```bash
python -m poe_build tools                                   # list commands and their arguments
python -m poe_build info                                    # classes, ascendancies, bloodlines, counts
python -m poe_build search_gems --query "cold projectile"
python -m poe_build compatible_supports --active "Ice Shot"
python -m poe_build search_uniques --query "life leech" --category ring
python -m poe_build search_passives --query "minion damage" --kind notable
python -m poe_build search_mods --base "Hubris Circlet" --query "energy shield"
python -m poe_build new_build --name "Ice Shot Deadeye" --class_name Ranger --ascendancy Deadeye --level 92
python -m poe_build allocate --name "Ice Shot Deadeye" --nodes "Point Blank" "Far Shot"
python -m poe_build add_skill_group --name "Ice Shot Deadeye" --gems "Ice Shot" "Mirage Archer" --slot "Body Armour" --main true
python -m poe_build equip_unique --name "Ice Shot Deadeye" --slot "Weapon 1" --unique "Lioneye's Glare"
python -m poe_build equip_rare --name "..." --slot Helmet --base "Lion Pelt" --mods "+(80-89) to maximum Life" "+(36-41)% to Cold Resistance"
python -m poe_build show_build --name "Ice Shot Deadeye"    # includes 'problems' to fix
python -m poe_build build_stats --name "..."                # DPS + defences + problems + every stat line summed
python -m poe_build what_if --name "..." --allocate "Heart of Flame" --equip_unique '{"Amulet":"Astramentis"}'  # try before saving
python -m poe_build suggest_passives --name "..." --objective dps --max_cost 6   # best nodes per point
python -m poe_build search_config --query "power charges"   # PoB config options
python -m poe_build set_config --name "..." --values '{"usePowerCharges": true, "enemyIsBoss": "Uber"}'
python -m poe_build set_gem --name "..." --group 1 --gem "Spell Echo" --level 21 --quality 20
python -m poe_build inspect_item --unique "Kaom's Heart"    # how each mod line is parsed
python -m poe_build import_character --account "Name#1234" --character "MyChar"
python -m poe_build export_pob --name "..."                 # code to import into Path of Building
python -m poe_build call allocate '{"name":"...","nodes":["Acrobatics"]}'   # JSON form of any call
```

Workflow: search → `new_build` → `allocate` (the shortest path is found for you; use `path_to` to check the cost) → masteries via `allocate` with `mastery_effect` → gems → items → `set_config` (charges, conditions the build really has) → `show_build`. Use `what_if` / `suggest_passives` to optimise. Fix every entry in `problems` (uncapped resists, attribute/gem requirements, too many points, mana), then `export_pob`.

Rules:
- Never guess names. Look them up with the search tools, because names must match the data exactly.
- For rares, pick mods from `search_mods` on that base so the item could actually roll.
- DPS/defence come from `poe_build/calc.py`, which uses PoB's own data and formulas. It does not model minion DPS, trigger rates, cluster/timeless jewel trees or special-case uniques. `unparsed_mods` / `notes` in the output tell you what was skipped. Say so when it matters, and suggest checking the `export_pob` code in Path of Building.
- Default enemy is a Pinnacle boss (50% elemental / 30% chaos res, level 84), the same as PoB.
- Builds are stored as JSON in `builds/`. The web planner (`python -m poe_build web`) refreshes live while you edit.

## Data
`poe_build/data/*.json.gz` is generated from PathOfBuildingCommunity/PathOfBuilding by `tools/build_data.py`, which only parses the Lua and never runs it. See the README for how to refresh it. Tests: `python -m unittest discover tests`.

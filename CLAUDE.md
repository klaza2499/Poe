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
python -m poe_build build_stats --name "..."                # attributes, life/ES, resists, every stat line summed
python -m poe_build export_pob --name "..."                 # code to import into Path of Building
python -m poe_build call allocate '{"name":"...","nodes":["Acrobatics"]}'   # JSON form of any call
```

Workflow: search → `new_build` → `allocate` (the shortest path is found for you; use `path_to` to check the cost) → masteries via `allocate` with `mastery_effect` → gems → items → `show_build`. Fix every entry in `problems` (uncapped resists, attribute requirements, too many points), then `export_pob`.

Rules:
- Never guess names. Look them up with the search tools, because names must match the data exactly.
- For rares, pick mods from `search_mods` on that base so the item could actually roll.
- `build_stats` is a quick estimate. It has no DPS or conditional buffs. For exact numbers, tell the user to import the `export_pob` code into Path of Building.
- Builds are stored as JSON in `builds/`. The web planner (`python -m poe_build web`) refreshes live while you edit.

## Data
`poe_build/data/*.json.gz` is generated from PathOfBuildingCommunity/PathOfBuilding by `tools/build_data.py`, which only parses the Lua and never runs it. See the README for how to refresh it. Tests: `python -m unittest discover tests`.

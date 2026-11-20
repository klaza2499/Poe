#!/usr/bin/env bash
# Refresh poe_build/data from the latest Path of Building Community data.
# Usage: tools/update_data.sh [tree_version]   e.g. tools/update_data.sh 3_30
set -euo pipefail
cd "$(dirname "$0")/.."
TREE="${1:-3_30}"
WORK="$(mktemp -d)"
git clone --depth 1 --filter=blob:none --sparse https://github.com/PathOfBuildingCommunity/PathOfBuilding.git "$WORK/pob"
git -C "$WORK/pob" sparse-checkout set --no-cone src/Data "src/TreeData/$TREE/tree.lua" src/Modules/ConfigOptions.lua
python3 -I tools/build_data.py "$WORK/pob/src" --tree "$TREE"
rm -rf "$WORK"
echo "done - run: python3 -m unittest discover tests"

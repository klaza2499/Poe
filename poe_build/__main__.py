"""Command line: python -m poe_build <command> [options]

  python -m poe_build tools                       list every command
  python -m poe_build search_gems --query "fire projectile"
  python -m poe_build new_build --name "RF Jugg" --class_name Juggernaut
  python -m poe_build allocate --name "RF Jugg" --nodes "Unbreakable" "Resolute Technique"
  python -m poe_build call allocate '{"name": "RF Jugg", "nodes": ["Unbreakable"]}'
  python -m poe_build mcp                         run the MCP server (stdio) for Claude
  python -m poe_build web [--port 8765]           open the web planner
"""
from __future__ import annotations

import argparse
import json
import sys

from . import api


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="poe_build", description="PoE build planner for humans and Claude")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("tools", help="list commands")
    c = sub.add_parser("call", help="call a tool with JSON arguments")
    c.add_argument("tool")
    c.add_argument("json", nargs="?", default="{}")
    sub.add_parser("mcp", help="run MCP stdio server")
    w = sub.add_parser("web", help="run web planner")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--no-browser", action="store_true")
    for t in api.TOOLS:
        p = sub.add_parser(t["name"], help=t["description"][:80])
        for pname, (ptype, pdesc, req) in t["params"].items():
            kw = {"help": pdesc, "required": req}
            if ptype == "array":
                kw["nargs"] = "+"
            elif ptype == "integer":
                kw["type"] = int
            elif ptype == "object":
                kw["type"] = json.loads
                kw["help"] = pdesc + " (JSON)"
            elif ptype == "boolean":
                kw = {"help": pdesc, "type": lambda s: s.lower() in ("1", "true", "yes", "y")}
            p.add_argument(f"--{pname}", **kw)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    if a.cmd == "tools":
        for t in api.TOOLS:
            ps = " ".join(f"--{k}" + ("" if v[2] else "?") for k, v in t["params"].items())
            print(f"{t['name']:22} {ps}\n    {t['description']}")
        return 0
    if a.cmd == "mcp":
        from . import mcp_server
        mcp_server.serve()
        return 0
    if a.cmd == "web":
        from . import web
        web.serve(a.host, a.port, open_browser=not a.no_browser)
        return 0
    try:
        if a.cmd == "call":
            res = api.call(a.tool, json.loads(a.json))
        else:
            args = {k: v for k, v in vars(a).items() if k != "cmd" and v is not None}
            res = api.call(a.cmd, args)
        _print(res)
        return 0
    except Exception as e:  # noqa
        _print({"error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())

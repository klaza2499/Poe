"""Minimal MCP (Model Context Protocol) server over stdio — no dependencies.

Lets Claude Desktop / Claude Code use every poe_build tool directly:
search gems/uniques/passives/mods, create builds, path the tree, equip items,
check stats and export a Path of Building code.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import api

PROTOCOL = "2025-06-18"

INSTRUCTIONS = """Path of Exile 1 build planner backed by Path of Building's game data.
Workflow: info -> new_build -> allocate (auto-paths) -> add_skill_group -> equip_unique / equip_rare
-> show_build / build_stats (fix 'problems': resists, attributes, points) -> export_pob.
Use search_* tools to look things up instead of guessing names; names must match game data.
Stats here are quick estimates; export_pob gives a code for exact DPS in Path of Building."""


def _schema(params):
    props, req = {}, []
    for k, (t, d, r) in params.items():
        p = {"type": t, "description": d}
        if t == "array":
            p["items"] = {"type": ["string", "integer", "object"]}
        props[k] = p
        if r:
            req.append(k)
    return {"type": "object", "properties": props, "required": req}


def _tools():
    return [{"name": t["name"], "description": t["description"], "inputSchema": _schema(t["params"])}
            for t in api.TOOLS]


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": ver, "capabilities": {"tools": {}},
            "serverInfo": {"name": "poe-build", "version": "1.0.0"}, "instructions": INSTRUCTIONS}}
    if method in ("notifications/initialized", "initialized") or (method or "").startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tools()}}
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            res = api.call(p.get("name"), p.get("arguments") or {})
            text = json.dumps(res, ensure_ascii=False, indent=1)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as e:  # tool errors go back to the model
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None):
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy codepage
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out = handle(msg)
        except Exception:
            out = {"jsonrpc": "2.0", "id": msg.get("id"),
                   "error": {"code": -32603, "message": traceback.format_exc(limit=2)}}
        if out is not None:
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()

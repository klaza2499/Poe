"""Local web planner: python -m poe_build web  ->  http://127.0.0.1:8765

Serves web/index.html plus a small JSON API that calls the same tools Claude uses
(POST /api/call {"tool": ..., "args": {...}}). The page polls the build file, so
edits made by Claude through MCP/CLI show up live.
"""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import api, data
from .build import BUILDS_DIR, safe_name

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_tree_cache = None


def tree_payload():
    global _tree_cache
    if _tree_cache is None:
        t = data.tree()
        nodes = []
        for nid, n in t["nodes"].items():
            if n.get("isProxy") or n.get("isClusterInner") or "x" not in n:
                continue
            if not n.get("adj") and n["type"] != "mastery":
                continue  # anoint-only / unused nodes are not on the visible tree
            e = {"id": int(nid), "n": n["name"], "t": n["type"], "x": n["x"], "y": n["y"], "s": n.get("stats", []),
                 "a": n.get("adj", [])}
            if n.get("ascendancy"):
                e["asc"] = n["ascendancy"]
            if n.get("masteryEffects"):
                e["me"] = n["masteryEffects"]
            if n.get("group") is not None:
                e["g"] = n["group"]
            nodes.append(e)
        _tree_cache = json.dumps({"version": t["version"], "classes": t["classes"], "bloodlines": t["bloodlines"],
                                  "nodes": nodes}, ensure_ascii=False).encode("utf-8")
    return _tree_cache


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/tree":
            return self._send(200, tree_payload())
        if u.path == "/api/build":
            name = q.get("name", [""])[0]
            try:
                p = os.path.join(BUILDS_DIR, safe_name(name) + ".json")
                with open(p, encoding="utf-8") as f:
                    raw = json.load(f)
                return self._send(200, {"raw": raw, "mtime": os.path.getmtime(p), "summary": api.show_build(name)})
            except Exception as e:
                return self._send(404, {"error": str(e)})
        if u.path == "/api/mtime":
            name = q.get("name", [""])[0]
            try:
                return self._send(200, {"mtime": os.path.getmtime(os.path.join(BUILDS_DIR, safe_name(name) + ".json")),
                                        "builds": api.list_builds()["builds"]})
            except Exception:
                return self._send(200, {"mtime": 0, "builds": api.list_builds()["builds"]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/call":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            return self._send(200, {"result": api.call(req["tool"], req.get("args") or {})})
        except Exception as e:
            return self._send(200, {"error": f"{type(e).__name__}: {e}"})


def serve(host="127.0.0.1", port=8765, open_browser=True):
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"PoE Build Planner running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

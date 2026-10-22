"""
The Simulation Runtime Service's HTTP API.

Two routes, deliberately minimal — both ends of this integration are ours (see the
package docstring), so there is no third-party REST surface to mirror the way
KogitoProxy/FlowableProxy do:

    GET  /health
    POST /processes/{process_id}/instances   body: {"variables": {...}}

`POST .../instances` is synchronous: it runs engine.run_instance() to completion
inside the request (same semantics as Kogito's `POST /{processId}` — see
KogitoProxy.start_process's docstring) and returns the terminal process_variables.

Stdlib only (`http.server.ThreadingHTTPServer`) — no new web-framework dependency for
two routes; see scripts/build_simpn_app.sh for why this service gets its own venv in
the first place. ThreadingHTTPServer runs each request in its own thread, so one
in-flight instance's step() loop doesn't block /health or a concurrent instance.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import engine


class _Handler(BaseHTTPRequestHandler):
    process: engine.ProcessModel
    simpn_params: dict

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter than the default
        sys.stderr.write(f"[simpn-runtime] {fmt % args}\n")

    def _send_json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "process_id": self.process.id})
            return
        self._send_json(404, {"error": f"not found: {self.path}"})

    def do_POST(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "processes" or parts[2] != "instances":
            self._send_json(404, {"error": f"not found: {self.path}"})
            return

        process_id = parts[1]
        if process_id != self.process.id:
            self._send_json(404, {"error": f"unknown process id: {process_id!r}"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        variables = body.get("variables", {})

        try:
            state = engine.run_instance(self.process, self.simpn_params, variables)
        except Exception as exc:  # the caller (SimPNProxy) surfaces this as a SimPNError
            self._send_json(500, {"error": str(exc)})
            return

        self._send_json(200, state["process_variables"])


def serve(process: engine.ProcessModel, simpn_params: dict, host: str, port: int) -> None:
    handler = type("Handler", (_Handler,), {"process": process, "simpn_params": simpn_params})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"[simpn-runtime] serving process '{process.id}' on http://{host}:{port}", file=sys.stderr)
    httpd.serve_forever()

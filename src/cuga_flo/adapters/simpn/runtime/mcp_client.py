"""
Minimal synchronous JSON-RPC client for CUGA FLO's MCP bridge.

Stdlib only (``urllib.request`` + ``json``) — this package is a standalone service
with its own isolated venv (see ``src/cuga_flo/adapters/simpn/__init__.py``) and must
not import ``cuga_flo`` or ``cuga``.

This is the Python-to-Python equivalent of ``CugaFlo.java``'s ``call()``
(``src/cuga_flo/adapters/kogito/runtime/CugaFlo.java``): FastMCP's stateless HTTP app
answers every ``tools/call`` with an SSE body (confirmed empirically — a plain
``Accept: application/json`` header does not change this), so the JSON-RPC envelope
arrives on a ``data:`` line. Unlike the Java client, this one reads the envelope's
``result.structuredContent`` directly instead of re-parsing ``content[0].text`` as
JSON — FastMCP already hands back the typed value there, keyed as ``{"result": ...}``
for a tool that returns a plain scalar/string (e.g. ``route_gateway``) and as the
object itself for a tool that returns a dict (``execute_task``, ``evaluate_hook``,
``complete_process``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class MCPCallError(RuntimeError):
    """Raised when an MCP tool call fails (HTTP error, JSON-RPC error, or isError)."""


def _unwrap_sse(body: str) -> str:
    """Pull the JSON out of an SSE body; pass a plain JSON body through untouched."""
    for line in body.splitlines():
        if line.startswith("data:"):
            return line[len("data:") :].strip()
    return body.strip()


def call_tool(mcp_url: str, tool: str, arguments: dict[str, Any], timeout: float = 300.0) -> Any:
    """
    Invoke an MCP tool and return its structured result.

    For a tool returning a dict, this is that dict. For a tool returning a plain
    value (route_gateway returns a flow-id string), FastMCP wraps it as
    ``{"result": <value>}``, which this unwraps back to the bare value.
    """
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        mcp_url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise MCPCallError(f"{tool} -> HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
    except urllib.error.URLError as exc:
        raise MCPCallError(f"{tool} -> connection failed: {exc}") from exc

    envelope = json.loads(_unwrap_sse(raw))
    if "error" in envelope:
        raise MCPCallError(f"{tool} -> JSON-RPC error: {envelope['error']}")

    result = envelope.get("result", {})
    if result.get("isError"):
        text = (result.get("content") or [{}])[0].get("text", "")
        raise MCPCallError(f"{tool} -> {text}")

    if "structuredContent" in result:
        structured = result["structuredContent"]
        # FastMCP wraps a non-object return value (route_gateway's str) this way.
        if isinstance(structured, dict) and list(structured.keys()) == ["result"]:
            return structured["result"]
        return structured

    # Fallback for a tool with no declared return annotation: parse content[0].text.
    text = (result.get("content") or [{}])[0].get("text", "")
    try:
        return json.loads(text)
    except ValueError:
        return text

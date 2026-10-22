"""
SimPNProxy - thin client over the standalone SimPN Simulation Runtime Service.

Targets a service built from an app's BPMN model + a `simpn-parameters.yaml` BPS
config via `scripts/build_simpn_app.sh`, then started with its generated `run.sh`
(see `src/cuga_flo/adapters/simpn/runtime/`).

The service exposes exactly two routes (see runtime/server.py):

    GET  /health
    POST /processes/{process_id}/instances   start an instance (body = process variables)

Unlike Kogito/Flowable, both ends of this integration are ours, so the service's API
is deliberately minimal rather than mirroring a third-party REST surface. Like Kogito,
there is no `deploy()` — the service compiles its process model at build time
(scripts/build_simpn_app.sh), so a process must already be built into the running
service.

Quick CLI (against a running service):

    python -m cuga_flo.adapters.simpn.proxy ping
    python -m cuga_flo.adapters.simpn.proxy start loan_approval_simpn
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv
from loguru import logger

# Load .env so config (base URL) lives there, not in code.
load_dotenv(find_dotenv(usecwd=True))

# Distinct from Flowable's 8080 and Kogito's 8081 — the three engines can run side by
# side. Real values belong in .env (SIMPN_* keys) or the app's workflow_engine.url.
_FALLBACK_BASE_URL = "http://localhost:8082"
# `POST /processes/{id}/instances` is SYNCHRONOUS: the service does not answer until
# the simulated instance completes. Every CUGA FLO control point on the way is an LLM
# call of 10-60s, so a modest process routinely needs minutes end to end — see
# KogitoProxy._FALLBACK_TIMEOUT, which this mirrors for the same reason.
_FALLBACK_TIMEOUT = 1200.0


class SimPNError(RuntimeError):
    """Raised when the Simulation Runtime Service returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SimPNProxy:
    """
    Synchronous client for the standalone SimPN Simulation Runtime Service.

    Example:
        proxy = SimPNProxy()
        result = proxy.start_process("loan_approval_simpn", variables={"loan_amount": 1000})
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        base_url = base_url or os.getenv("SIMPN_BASE_URL", _FALLBACK_BASE_URL)
        timeout = timeout if timeout is not None else float(os.getenv("SIMPN_TIMEOUT", _FALLBACK_TIMEOUT))

        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def __enter__(self) -> SimPNProxy:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("/") else f"/{path}"
        logger.debug("SimPN {} {}", method, url)
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise SimPNError(f"Request to SimPN runtime service failed: {exc}") from exc

        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise SimPNError(
                f"SimPN {method} {url} -> {resp.status_code}: {body}",
                status_code=resp.status_code,
                body=body,
            )

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def ping(self) -> bool:
        """Return True if the runtime service is reachable."""
        try:
            self._request("GET", "/health")
            return True
        except SimPNError as exc:
            logger.warning("SimPN ping failed: {}", exc)
            return False

    def start_process(
        self,
        process_id: str,
        variables: dict[str, Any] | None = None,
    ) -> dict:
        """
        Start and run a simulated instance of `process_id` to completion.

        Blocks until the instance finishes — the service runs its DecProblem's `step()`
        loop for this instance synchronously inside the request, calling back into CUGA
        FLO's MCP bridge at each agentic control point. The response body holds the
        terminal process variables, mirroring KogitoProxy.start_process.
        """
        result = self._request(
            "POST", f"/processes/{process_id}/instances", json={"variables": variables or {}}
        )
        logger.info("Started SimPN process '{}'", process_id)
        return result or {}


def _print(obj: Any) -> None:
    import json

    print(json.dumps(obj, indent=2, default=str))


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0

    cmd, *rest = argv
    proxy = SimPNProxy()

    if cmd == "ping":
        ok = proxy.ping()
        print("reachable" if ok else "unreachable")
        return 0 if ok else 1

    if cmd == "start":
        if not rest:
            print("usage: start <process_id>")
            return 2
        _print(proxy.start_process(rest[0]))
        return 0

    print(f"unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

"""Apply the vendored cuga-agent patches to the *installed* `cuga` package.

CUGA FLO needs a few seams in cuga-agent core (supervisor delegation to FlowAgent,
`type: flow_agent` in supervisor configs, FlowAgent step-draining in the agent loop)
plus one behaviour-neutral fix. Rather than fork cuga-agent, the diffs live in
`patches/` and are stamped onto the installed package here.

    cuga-flo patch-host            apply (idempotent)
    cuga-flo patch-host --check    exit non-zero if not applied, or cuga-agent changed
    cuga-flo patch-host --revert   undo

An editable cuga-agent install (the normal dev setup) is a git checkout, so `apply`
restores the four target files to HEAD and re-applies all patches with `git apply --3way`
— fully idempotent, and tolerant of cuga-agent drift as long as the flow-specific
regions themselves do not conflict. A wheel install falls back to `patch(1) --forward`.

A marker next to `cuga/__init__.py` records the cuga-agent version and each patch's
sha256. `pip install -U cuga` reverts the edits and bumps the version; `--check` catches
it, and `cuga-flo start` runs `--check` before doing anything.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

PATCHES_DIR = Path(__file__).resolve().parent.parent.parent / "patches"
MARKER_NAME = ".cuga_flo_hostpatch.json"

# A string each patch introduces — used for a non-destructive "is it applied?" check.
SIGNATURES = {
    "01-supervisor-delegation-flowagent.patch": "HAS_FLOW_AGENT",
    "02-supervisor-config-flow-agent-type.patch": "Instantiating inline FlowAgent",
    "03-agent-loop-flow-step-drain.patch": "_FLOW_STEP_PREFIXES",
    "06-llm-http-client-timeout.patch": "does not propagate the",
}


def _target() -> dict:
    spec = importlib.util.find_spec("cuga")
    if spec is None or not spec.origin:
        sys.exit("cuga-flo: the 'cuga' (cuga-agent) package is not importable — install it first.")
    cuga_dir = Path(spec.origin).parent
    marker = cuga_dir / MARKER_NAME
    for d in (cuga_dir, *cuga_dir.parents):
        if (d / ".git").exists():
            return {"mode": "git", "root": d, "marker": marker, "files": _patch_targets()}
        if d.name == "site-packages":
            break
    return {"mode": "patch", "root": cuga_dir.parent, "strip": 2, "marker": marker,
            "files": _patch_targets()}


def _patches() -> list[Path]:
    files = sorted(PATCHES_DIR.glob("*.patch"))
    if not files:
        sys.exit(f"cuga-flo: no patches found under {PATCHES_DIR}")
    return files


def _patch_targets() -> list[str]:
    seen: list[str] = []
    for p in _patches():
        for m in re.finditer(r"^\+\+\+ b/(.+)$", p.read_text(), re.M):
            if m.group(1) not in seen:
                seen.append(m.group(1))
    return seen


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _cuga_version() -> str:
    try:
        return importlib.metadata.version("cuga")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _signatures_present(t: dict) -> bool:
    for p in _patches():
        sig = SIGNATURES.get(p.name)
        if not sig:
            continue
        targets = [m.group(1) for m in re.finditer(r"^\+\+\+ b/(.+)$", p.read_text(), re.M)]
        if not any(sig in (t["root"] / tgt).read_text() for tgt in targets if (t["root"] / tgt).exists()):
            return False
    return True


def check(verbose: bool = True) -> bool:
    t = _target()
    marker: Path = t["marker"]
    if not marker.exists():
        if verbose:
            print("cuga-flo: host patches NOT applied — run `cuga-flo patch-host`.")
        return False
    state = json.loads(marker.read_text())
    ver = _cuga_version()
    if state.get("cuga_version") != ver:
        if verbose:
            print(f"cuga-flo: cuga-agent changed since patching "
                  f"({state.get('cuga_version')} -> {ver}); re-run `cuga-flo patch-host`.")
        return False
    if state.get("patches") != {p.name: _sha(p) for p in _patches()}:
        if verbose:
            print("cuga-flo: vendored patch set changed; re-run `cuga-flo patch-host`.")
        return False
    if not _signatures_present(t):
        if verbose:
            print("cuga-flo: a host patch is no longer present in cuga-agent; re-run `cuga-flo patch-host`.")
        return False
    if verbose:
        print(f"cuga-flo: host patches OK ({len(_patches())} applied against cuga-agent {ver}).")
    return True


def apply() -> None:
    t = _target()
    patches = _patches()

    if t["mode"] == "git":
        # restore-then-reapply == idempotent, drift-tolerant
        r = _git(t["root"], "checkout", "HEAD", "--", *t["files"])
        if r.returncode != 0:
            sys.exit(f"cuga-flo: could not restore cuga-agent target files:\n{r.stderr}")
        for p in patches:
            r = _git(t["root"], "apply", "--3way", "--whitespace=nowarn", str(p))
            conflicted = _git(t["root"], "grep", "-l", "-e", "^<<<<<<< ", "--", "*.py").stdout.strip()
            if r.returncode != 0 or conflicted:
                _git(t["root"], "checkout", "HEAD", "--", *t["files"])
                sys.exit(f"cuga-flo: {p.name} does not apply to cuga-agent {_cuga_version()}.\n"
                         f"The flow-specific region has drifted; the patch needs updating.\n"
                         f"{r.stderr}{('conflicts in ' + conflicted) if conflicted else ''}")
    else:
        for i, p in enumerate(patches):
            already = subprocess.run(
                ["patch", f"-p{t['strip']}", "-R", "--dry-run", "--force", "-i", str(p)],
                cwd=t["root"], capture_output=True, text=True,
            ).returncode == 0
            if already:
                continue
            r = subprocess.run(
                ["patch", f"-p{t['strip']}", "--forward", "--fuzz=3", "-i", str(p)],
                cwd=t["root"], capture_output=True, text=True,
            )
            if r.returncode != 0:
                for q in patches[:i]:
                    subprocess.run(["patch", f"-p{t['strip']}", "-R", "--forward", "-i", str(q)],
                                   cwd=t["root"], capture_output=True, text=True)
                sys.exit(f"cuga-flo: {p.name} does not apply to cuga-agent {_cuga_version()}.\n{r.stdout}{r.stderr}")

    t["marker"].write_text(json.dumps(
        {"cuga_version": _cuga_version(), "patches": {p.name: _sha(p) for p in patches}}, indent=2
    ))
    print(f"cuga-flo: applied {len(patches)} host patch(es) to cuga-agent {_cuga_version()} ({t['mode']} mode).")


def revert() -> None:
    t = _target()
    if t["mode"] == "git":
        _git(t["root"], "checkout", "HEAD", "--", *t["files"])
    else:
        for p in reversed(_patches()):
            subprocess.run(["patch", f"-p{t['strip']}", "-R", "--forward", "-i", str(p)],
                           cwd=t["root"], capture_output=True, text=True)
    t["marker"].unlink(missing_ok=True)
    print("cuga-flo: host patches reverted.")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        sys.exit(0 if check() else 1)
    if "--revert" in argv:
        revert()
        return
    apply()


if __name__ == "__main__":
    main()

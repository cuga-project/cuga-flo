"""Apply the vendored cuga-agent patches to the *installed* `cuga` package.

CUGA FLO needs a few seams in cuga-agent core (supervisor delegation to FlowAgent,
`type: flow_agent` in supervisor configs, FlowAgent step-draining in the agent loop)
plus two behaviour-neutral fixes. Rather than fork cuga-agent, the diffs live in
`patches/` and are stamped onto the installed package here.

`cuga-flo patch-host`            apply (idempotent)
`cuga-flo patch-host --check`    exit non-zero if not applied, or cuga-agent changed
`cuga-flo patch-host --revert`   undo

A marker file next to `cuga/__init__.py` records the cuga-agent version and the sha256
of every patch applied. `pip install -U cuga` silently reverts the edits and bumps the
version — `--check` catches that, and `cuga-flo start` runs `--check` before doing anything.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PATCHES_DIR = Path(__file__).resolve().parent.parent.parent / "patches"
MARKER_NAME = ".cuga_flo_hostpatch.json"


def _cuga_paths() -> tuple[Path, Path, int]:
    """Return (marker_path, patch_base_dir, strip_level) for the installed cuga package."""
    spec = importlib.util.find_spec("cuga")
    if spec is None or not spec.origin:
        sys.exit("cuga-flo: the 'cuga' (cuga-agent) package is not importable — install it first.")
    cuga_dir = Path(spec.origin).parent
    parent = cuga_dir.parent
    if parent.name == "src":
        # editable install: patch paths are 'src/cuga/...', apply from the checkout root
        return cuga_dir / MARKER_NAME, parent.parent, 1
    # wheel install: strip 'a/src/' -> 'cuga/...', apply from site-packages
    return cuga_dir / MARKER_NAME, parent, 2


def _patches() -> list[Path]:
    files = sorted(PATCHES_DIR.glob("*.patch"))
    if not files:
        sys.exit(f"cuga-flo: no patches found under {PATCHES_DIR}")
    return files


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _cuga_version() -> str:
    try:
        return importlib.metadata.version("cuga")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _run_patch(patch: Path, base: Path, strip: int, revert: bool = False, dry: bool = False) -> bool:
    args = ["patch", f"-p{strip}", "--forward", "--fuzz=3", "-i", str(patch)]
    if revert:
        args.insert(1, "-R")
    if dry:
        args.append("--dry-run")
    r = subprocess.run(args, cwd=base, capture_output=True, text=True)
    return r.returncode == 0


def check(verbose: bool = True) -> bool:
    marker, base, strip = _cuga_paths()
    if not marker.exists():
        if verbose:
            print("cuga-flo: host patches NOT applied — run `cuga-flo patch-host`.")
        return False
    state = json.loads(marker.read_text())
    ver = _cuga_version()
    if state.get("cuga_version") != ver:
        if verbose:
            print(
                f"cuga-flo: cuga-agent changed since patching "
                f"({state.get('cuga_version')} -> {ver}); re-run `cuga-flo patch-host`."
            )
        return False
    want = {p.name: _sha(p) for p in _patches()}
    if state.get("patches") != want:
        if verbose:
            print("cuga-flo: vendored patch set changed since it was applied; re-run `cuga-flo patch-host`.")
        return False
    # confirm each patch is still present in the tree (reverse-dry-run must succeed)
    for p in _patches():
        if not _run_patch(p, base, strip, revert=True, dry=True):
            if verbose:
                print(f"cuga-flo: {p.name} is no longer applied to cuga-agent; re-run `cuga-flo patch-host`.")
            return False
    if verbose:
        print(f"cuga-flo: host patches OK ({len(want)} applied against cuga-agent {ver}).")
    return True


def apply() -> None:
    marker, base, strip = _cuga_paths()
    patches = _patches()
    if marker.exists() and check(verbose=False):
        print(f"cuga-flo: host patches already applied ({len(patches)}). Nothing to do.")
        return
    applied: list[str] = []
    for p in patches:
        if _run_patch(p, base, strip, revert=True, dry=True):
            applied.append(p.name)  # already applied
            continue
        if not _run_patch(p, base, strip, dry=True):
            _rollback(applied, base, strip)
            sys.exit(f"cuga-flo: {p.name} does not apply cleanly to cuga-agent {_cuga_version()}. "
                     f"cuga-agent has moved too far; the patch needs updating.")
        if not _run_patch(p, base, strip):
            _rollback(applied, base, strip)
            sys.exit(f"cuga-flo: {p.name} failed to apply.")
        applied.append(p.name)
    marker.write_text(json.dumps(
        {"cuga_version": _cuga_version(), "patches": {p.name: _sha(p) for p in patches}},
        indent=2,
    ))
    print(f"cuga-flo: applied {len(patches)} host patch(es) to cuga-agent {_cuga_version()}.")


def revert() -> None:
    marker, base, strip = _cuga_paths()
    for p in reversed(_patches()):
        _run_patch(p, base, strip, revert=True)
    marker.unlink(missing_ok=True)
    print("cuga-flo: host patches reverted.")


def _rollback(names: list[str], base: Path, strip: int) -> None:
    by_name = {p.name: p for p in _patches()}
    for n in reversed(names):
        _run_patch(by_name[n], base, strip, revert=True)


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

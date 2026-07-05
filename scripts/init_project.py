"""Initialize a checkout: install the project's pinned runtime dependencies.

Mail Analyzer 2.0 depends on a small, fixed set of third-party packages
(recorded in ``docs/dependencies.md``). This script installs them from the
version-pinned ``requirements.txt`` at the project root, into the interpreter
that runs the script, so a fresh checkout comes up with one command and everyone
lands on the same versions.

``requirements.txt`` is the single source of truth for versions — edit the pins
there, not in this script. pywin32 carries a ``sys_platform == "win32"`` marker,
so pip skips it automatically on non-Windows boxes: the app runs without Outlook.

Because a real install mutates the environment and reaches the network, the
script prints its plan first and never installs under ``--dry-run`` / ``--check``.

Usage::

    python scripts/init_project.py               # install pinned requirements
    python scripts/init_project.py --dry-run     # show the plan, install nothing
    python scripts/init_project.py --check        # report installed vs required
    python scripts/init_project.py --upgrade-pip  # upgrade pip first, then install

Exit codes: 0 on success; 1 on a failed install, a missing requirements file, or
(under ``--check``) any missing/mismatched package.
"""

import argparse
import importlib.metadata as metadata
import platform
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

# The only marker forms this project's requirements use. Anything more exotic is
# deferred to pip, which is the real authority at install time.
_MARKER_RE = re.compile(r"""^(\w+)\s*(==|!=)\s*['"]([^'"]+)['"]$""")
_NAME_RE = re.compile(r"^([A-Za-z0-9_.\-]+)")


class Requirement:
    """One parsed requirement line: name, version pin, and platform marker."""

    def __init__(self, name, version, marker):
        self.name = name
        self.version = version  # e.g. "==308", or "" if unpinned
        self.marker = marker
        self.applies, self.skip_reason = _marker_applies(marker)

    @property
    def pinned_version(self):
        """The bare version behind an ``==`` pin, else ``None``."""
        return self.version[2:].strip() if self.version.startswith("==") else None


def _marker_applies(marker):
    """Return ``(applies, reason_if_not)`` for a requirement's environment marker.

    Evaluates the ``sys_platform`` / ``platform_system`` equality markers this
    project uses; any other (or missing) marker is treated as applicable and left
    for pip to judge at install time.
    """
    if not marker:
        return True, ""
    m = _MARKER_RE.match(marker)
    if not m:
        return True, ""
    key, op, value = m.groups()
    env = {"sys_platform": sys.platform, "platform_system": platform.system()}
    actual = env.get(key)
    if actual is None:
        return True, ""
    ok = (actual == value) if op == "==" else (actual != value)
    reason = "" if ok else f'{key}={actual!r} does not satisfy marker "{marker}"'
    return ok, reason


def parse_requirements(text):
    """Parse ``requirements.txt`` text into a list of :class:`Requirement`."""
    reqs = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        spec, _, marker = line.partition(";")
        spec, marker = spec.strip(), marker.strip()
        name_match = _NAME_RE.match(spec)
        if not name_match:
            continue
        name = name_match.group(1)
        version = spec[len(name):].strip()
        reqs.append(Requirement(name, version, marker))
    return reqs


def load_requirements():
    """Read and parse the pinned requirements file, or exit 1 if it is missing."""
    if not REQUIREMENTS_FILE.exists():
        print(f"No requirements file at {REQUIREMENTS_FILE}", file=sys.stderr)
        return None
    return parse_requirements(REQUIREMENTS_FILE.read_text(encoding="utf-8"))


def installed_version(name):
    """Installed version of ``name``, or ``None`` if it is not importable."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def print_plan(reqs):
    """Show which pins will be installed and which are skipped on this platform."""
    print(f"Python {platform.python_version()} at {sys.executable}")
    print(f"Requirements: {REQUIREMENTS_FILE}")
    for req in reqs:
        if req.applies:
            print(f"  install  {req.name}{req.version or ' (unpinned)'}")
        else:
            print(f"  skip     {req.name}{req.version} - {req.skip_reason}")


def run_check(reqs):
    """Report installed vs pinned versions; return 0 only if all applicable match."""
    mismatched = 0
    for req in reqs:
        if not req.applies:
            print(f"  skip      {req.name} - {req.skip_reason}")
            continue
        have = installed_version(req.name)
        want = req.pinned_version
        if have is None:
            print(f"  MISSING   {req.name} (want {want or 'any'})")
            mismatched += 1
        elif want and have != want:
            print(f"  MISMATCH  {req.name} {have} (want {want})")
            mismatched += 1
        else:
            print(f"  ok        {req.name} {have}")
    if mismatched:
        print(f"{mismatched} package(s) missing or mismatched - run without --check to fix.")
        return 1
    print("All applicable requirements satisfied.")
    return 0


def pip_install(upgrade_pip):
    """Install the requirements file into the running interpreter via pip."""
    if upgrade_pip:
        print("Upgrading pip...")
        rc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
            check=False,
        ).returncode
        if rc != 0:
            print("pip upgrade failed.", file=sys.stderr)
            return rc
    print("Installing pinned requirements...")
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
        check=False,
    ).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install the project's pinned runtime dependencies.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="show the install plan without installing anything",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="report installed vs required versions without installing",
    )
    parser.add_argument(
        "--upgrade-pip",
        action="store_true",
        help="upgrade pip before installing (ignored with --dry-run/--check)",
    )
    args = parser.parse_args(argv)

    reqs = load_requirements()
    if reqs is None:
        return 1

    if args.check:
        return run_check(reqs)

    print_plan(reqs)
    if args.dry_run:
        print("[dry-run] Nothing installed.")
        return 0

    rc = pip_install(args.upgrade_pip)
    if rc != 0:
        print("Install failed.", file=sys.stderr)
        return rc
    print("Setup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

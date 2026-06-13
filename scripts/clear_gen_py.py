"""Delete the local win32com ``gen_py`` cache.

A pywin32 COM dispatch (``win32com.client.gencache.EnsureDispatch`` in
``mailfilter/outlook.py``) writes generated type-library wrappers into a
``gen_py`` folder under the system temp dir, e.g. ``%TEMP%\\gen_py\\<pyver>``.
After Outlook (or any other COM server) is updated, those cached wrappers can
go stale and dispatch starts failing with ``AttributeError`` / ``com_error``.
Deleting ``gen_py`` forces it to regenerate cleanly on the next run.

Usage::

    python scripts/clear_gen_py.py            # delete the cache
    python scripts/clear_gen_py.py --dry-run  # show what would be deleted

Safe to run on a machine without pywin32 or without the cache: it simply
reports that there is nothing to delete.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


def gen_py_root():
    """Best-effort path to the top-level ``gen_py`` cache folder.

    Prefers the location pywin32 itself resolved (``win32com.__gen_path__``,
    which points at the versioned subfolder) and climbs to its ``gen_py``
    ancestor. Falls back to the default ``<temp>/gen_py`` when pywin32 is not
    importable.
    """
    try:
        import win32com

        resolved = getattr(win32com, "__gen_path__", "") or ""
        if resolved:
            path = Path(resolved)
            for candidate in (path, *path.parents):
                if candidate.name == "gen_py":
                    return candidate
            return path  # unexpected layout: delete whatever was resolved
    except ImportError:
        pass
    return Path(tempfile.gettempdir()) / "gen_py"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Delete the win32com gen_py cache.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the target without deleting anything",
    )
    args = parser.parse_args(argv)

    target = gen_py_root()

    if not target.exists():
        print(f"Nothing to delete - no gen_py cache at {target}")
        return 0

    if args.dry_run:
        print(f"[dry-run] Would delete gen_py cache at {target}")
        return 0

    try:
        shutil.rmtree(target)
    except OSError as e:
        print(f"Failed to delete {target}: {e}", file=sys.stderr)
        return 1

    print(f"Deleted gen_py cache at {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

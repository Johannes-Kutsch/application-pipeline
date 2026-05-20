from __future__ import annotations

import importlib.resources
import importlib.resources.abc
from pathlib import Path

_EXCLUDE_DIRS = frozenset({"prompts"})
# Directories whose contents are user-authored and never overwritten on refresh.
_PRESERVE_DIRS = frozenset({"user-info", "latex"})
# Top-level files that are user-authored and never overwritten on refresh.
_PRESERVE_FILES = frozenset({"config.py", "layout.py"})


def init(target_dir: Path, *, refresh: bool = False) -> None:
    pkg = importlib.resources.files("application_pipeline.templates")
    _seed(pkg, target_dir, Path(), refresh=refresh)


def _seed(
    node: importlib.resources.abc.Traversable,
    target_dir: Path,
    rel: Path,
    *,
    refresh: bool,
) -> None:
    for item in node.iterdir():
        if item.name.startswith("__"):
            continue
        item_rel = rel / item.name
        if item.is_dir():
            if item.name in _EXCLUDE_DIRS:
                continue
            _seed(item, target_dir, item_rel, refresh=refresh)
        else:
            dest = target_dir / item_rel
            display = item_rel.as_posix()
            overwrite = refresh and _is_global(item_rel)
            if dest.exists():
                if overwrite:
                    dest.write_bytes(item.read_bytes())
                    print(f"overwrote {display}")
                elif refresh:
                    print(f"skipped {display} (preserved)")
                else:
                    print(f"skipped {display} (already exists)")
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item.read_bytes())
                print(f"wrote {display}")


def _is_global(rel: Path) -> bool:
    """Return True for files that --refresh should overwrite."""
    parts = rel.parts
    if len(parts) == 1 and parts[0] in _PRESERVE_FILES:
        return False
    if parts[0] in _PRESERVE_DIRS:
        return False
    return True

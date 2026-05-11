from __future__ import annotations

import importlib.resources
import importlib.resources.abc
from pathlib import Path


def init(target_dir: Path) -> None:
    pkg = importlib.resources.files("application_pipeline.templates")
    _seed(pkg, target_dir, Path())


def _seed(
    node: importlib.resources.abc.Traversable, target_dir: Path, rel: Path
) -> None:
    for item in node.iterdir():
        if item.name.startswith("__"):
            continue
        item_rel = rel / item.name
        if item.is_dir():
            _seed(item, target_dir, item_rel)
        else:
            dest = target_dir / item_rel
            if dest.exists():
                print(f"skipped {item_rel} (already exists)")
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item.read_bytes())
                print(f"wrote {item_rel}")

from __future__ import annotations

import importlib.resources
from pathlib import Path


def init(target_dir: Path) -> None:
    pkg = importlib.resources.files("application_pipeline.templates")
    for name in ("config.py", "layout.py"):
        dest = target_dir / name
        if dest.exists():
            print(f"skipped {name} (already exists)")
        else:
            data = (pkg / name).read_bytes()
            dest.write_bytes(data)
            print(f"wrote {name}")

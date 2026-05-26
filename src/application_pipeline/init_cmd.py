from __future__ import annotations

import importlib.resources
import importlib.resources.abc
from pathlib import Path

# Top-level files never seeded (retired; kept as user-space only if operator placed them there).
_EXCLUDE_FILES = frozenset({"layout.py"})
# Directories whose contents are user-authored and never overwritten on refresh.
# (Applied within the `application-pipeline` bucket only.)
_PRESERVE_DIRS = frozenset({"user-info"})
# Top-level files that are user-authored and never overwritten on refresh.
# (Applied within the `application-pipeline` bucket only.)
_PRESERVE_FILES = frozenset({"config.py", ".gitignore"})

# Templates subdirectories that are NOT routing buckets — package-internal,
# never seeded onto the host.
_NON_BUCKET_DIRS = frozenset({"prompts"})


def home_dir() -> Path:
    return Path.cwd() / "application-pipeline"


def missing_config_message(cwd: Path) -> str:
    """Error shown when application-pipeline/config.py can't be found from cwd."""
    if (cwd / "config.py").exists() and not (
        cwd / "application-pipeline" / "config.py"
    ).exists():
        return (
            f"you appear to be inside the data directory ({cwd})"
            " — run from its parent: cd .."
        )
    return (
        f"no application-pipeline/config.py in {cwd}"
        " — did you forget to cd, or run init?"
    )


def _bucket_roots(cwd: Path) -> dict[str, Path]:
    return {
        "application-pipeline": cwd / "application-pipeline",
        "claude": cwd / ".claude",
    }


def init(cwd: Path, *, refresh: bool = False) -> None:
    pkg = importlib.resources.files("application_pipeline.templates")
    roots = _bucket_roots(cwd)
    for bucket in pkg.iterdir():
        if bucket.name.startswith("__"):
            continue
        if not bucket.is_dir():
            continue
        if bucket.name in _NON_BUCKET_DIRS:
            continue
        if bucket.name not in roots:
            continue
        _seed(bucket, roots[bucket.name], Path(), refresh=refresh, bucket=bucket.name)

    if refresh:
        ap_root = roots["application-pipeline"]
        layout_path = ap_root / "layout.py"
        if layout_path.exists():
            layout_path.unlink()
            print("removed layout.py")
        _cleanup_legacy_skills_dir(ap_root)


def _cleanup_legacy_skills_dir(ap_root: Path) -> None:
    legacy_skills = ap_root / "skills"
    if not legacy_skills.is_dir():
        return
    legacy_skeleton = legacy_skills / "cv_skeleton.tex"
    if legacy_skeleton.exists():
        legacy_skeleton.unlink()
        print("removed skills/cv_skeleton.tex")
    try:
        legacy_skills.rmdir()
    except OSError:
        # User left other files inside — do not delete their content.
        return
    print("removed skills/")


def _seed(
    node: importlib.resources.abc.Traversable,
    target_dir: Path,
    rel: Path,
    *,
    refresh: bool,
    bucket: str,
) -> None:
    for item in node.iterdir():
        if item.name.startswith("__"):
            continue
        item_rel = rel / item.name
        if item.is_dir():
            _seed(item, target_dir, item_rel, refresh=refresh, bucket=bucket)
        else:
            if len(rel.parts) == 0 and item.name in _EXCLUDE_FILES:
                continue
            dest = target_dir / item_rel
            display = item_rel.as_posix()
            overwrite = refresh and _is_global(item_rel, bucket=bucket)
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


def _is_global(rel: Path, *, bucket: str) -> bool:
    """Return True for files that --refresh should overwrite."""
    if bucket == "application-pipeline":
        parts = rel.parts
        if len(parts) == 1 and parts[0] in _PRESERVE_FILES:
            return False
        if parts[0] in _PRESERVE_DIRS:
            return False
        return True
    # `claude` bucket: every package-shipped file is package-owned and
    # overwritten on refresh. Files in user-added skill dirs are not touched
    # because they don't appear in the templates tree at all.
    return True

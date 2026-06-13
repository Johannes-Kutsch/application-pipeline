from __future__ import annotations

import importlib.resources
import importlib.resources.abc
from pathlib import Path
from typing import NamedTuple

# Top-level files never seeded (retired; kept as user-space only if operator placed them there).
_EXCLUDE_FILES = frozenset({"layout.py"})
_RETIRED_REFRESH_PATHS: dict[str, tuple[Path, ...]] = {
    "application-pipeline": (Path("agent-skills/iterate-cv.md"),),
    "claude": (Path("skills/iterate-cv/SKILL.md"),),
    "codex": (Path("skills/iterate-cv/SKILL.md"),),
}


class _SeedPolicy(NamedTuple):
    bucket: str
    dest_root: Path
    operator_owned_roots: frozenset[str]
    operator_owned_top_level_files: frozenset[str]


def _seed_policies(cwd: Path) -> dict[str, _SeedPolicy]:
    return {
        "application-pipeline": _SeedPolicy(
            bucket="application-pipeline",
            dest_root=cwd / "application-pipeline",
            operator_owned_roots=frozenset({"user-info"}),
            operator_owned_top_level_files=frozenset({"config.py", ".gitignore"}),
        ),
        "claude": _SeedPolicy(
            bucket="claude",
            dest_root=cwd / ".claude",
            operator_owned_roots=frozenset(),
            operator_owned_top_level_files=frozenset(),
        ),
        "codex": _SeedPolicy(
            bucket="codex",
            dest_root=cwd / ".codex",
            operator_owned_roots=frozenset(),
            operator_owned_top_level_files=frozenset(),
        ),
    }


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


def init(cwd: Path, *, refresh: bool = False) -> None:
    pkg = importlib.resources.files("application_pipeline.templates")
    policies = _seed_policies(cwd)
    reports: list[tuple[str, str]] = []
    for bucket in pkg.iterdir():
        if bucket.name.startswith("__"):
            continue
        if not bucket.is_dir():
            continue
        policy = policies.get(bucket.name)
        if policy is None:
            continue
        reports.extend(
            _seed(bucket, policy.dest_root, Path(), refresh=refresh, policy=policy)
        )

    if refresh:
        ap_root = policies["application-pipeline"].dest_root
        layout_path = ap_root / "layout.py"
        if layout_path.exists():
            layout_path.unlink()
            reports.append(("removed", "layout.py"))
        reports.extend(_cleanup_retired_refresh_paths(policies))
        reports.extend(_cleanup_legacy_skills_dir(ap_root))

        visible = [(v, d) for v, d in reports if v in ("overwrote", "removed")]
        if visible:
            for verb, display in visible:
                print(f"{verb} {display}")
        else:
            print("directory is current — no files changed")
    else:
        wrote = sum(1 for v, _ in reports if v == "wrote")
        skipped = sum(1 for v, _ in reports if v == "skipped")
        if wrote and skipped:
            print(f"wrote {wrote} files, skipped {skipped}")
        elif wrote:
            print(f"wrote {wrote} files")
        else:
            print(f"skipped {skipped} files")


def _cleanup_legacy_skills_dir(ap_root: Path) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    legacy_skills = ap_root / "skills"
    if not legacy_skills.is_dir():
        return actions
    legacy_skeleton = legacy_skills / "cv_skeleton.tex"
    if legacy_skeleton.exists():
        legacy_skeleton.unlink()
        actions.append(("removed", "skills/cv_skeleton.tex"))
    try:
        legacy_skills.rmdir()
    except OSError:
        # User left other files inside — do not delete their content.
        return actions
    actions.append(("removed", "skills/"))
    return actions


def _cleanup_retired_refresh_paths(
    policies: dict[str, _SeedPolicy],
) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    for bucket, rel_paths in _RETIRED_REFRESH_PATHS.items():
        root = policies[bucket].dest_root
        for rel in rel_paths:
            dest = root / rel
            if not dest.exists():
                continue
            dest.unlink()
            actions.append(("removed", rel.as_posix()))
            _prune_empty_parents(root, dest.parent)
    return actions


def _prune_empty_parents(root: Path, node: Path) -> None:
    while node != root:
        try:
            node.rmdir()
        except OSError:
            return
        node = node.parent


def _seed(
    node: importlib.resources.abc.Traversable,
    target_dir: Path,
    rel: Path,
    *,
    refresh: bool,
    policy: _SeedPolicy,
) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    for item in node.iterdir():
        if item.name.startswith("__"):
            continue
        item_rel = rel / item.name
        if item.is_dir():
            actions.extend(
                _seed(item, target_dir, item_rel, refresh=refresh, policy=policy)
            )
        else:
            if len(rel.parts) == 0 and item.name in _EXCLUDE_FILES:
                continue
            dest = target_dir / item_rel
            display = item_rel.as_posix()
            overwrite = refresh and _is_package_owned(item_rel, policy=policy)
            if dest.exists():
                if overwrite:
                    template_bytes = item.read_bytes()
                    if dest.read_bytes() != template_bytes:
                        dest.write_bytes(template_bytes)
                        actions.append(("overwrote", display))
                    else:
                        actions.append(("unchanged", display))
                elif refresh:
                    actions.append(("preserved", display))
                else:
                    actions.append(("skipped", display))
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item.read_bytes())
                actions.append(("wrote", display))
    return actions


def _is_package_owned(rel: Path, *, policy: _SeedPolicy) -> bool:
    """Return True for package-owned refresh artefacts; False for operator-owned ones."""
    parts = rel.parts
    if len(parts) == 1 and parts[0] in policy.operator_owned_top_level_files:
        return False
    if parts and parts[0] in policy.operator_owned_roots:
        return False
    return True

from __future__ import annotations

import importlib.resources
import importlib.resources.abc
from pathlib import Path
from typing import Callable, Literal, NamedTuple

# Top-level files never seeded (retired; kept as user-space only if operator placed them there).
_EXCLUDE_FILES = frozenset({"layout.py"})
_EXCLUDE_ROOT_DIRS: dict[str, frozenset[str]] = {
    "application-pipeline": frozenset({"agent-skills"}),
}
_RETIRED_REFRESH_PATHS: dict[str, tuple[Path, ...]] = {
    "claude": (Path("skills/iterate-cv/SKILL.md"),),
    "codex": (Path("skills/iterate-cv/SKILL.md"),),
}
_OPERATOR_CREDENTIAL_PLACEHOLDER = b"OPENCODE_GO_API_KEY=\n"


class _SeedPolicy(NamedTuple):
    bucket: str
    dest_root: Path
    operator_owned_roots: frozenset[str]
    operator_owned_top_level_files: frozenset[str]
    package_owned: Callable[[Path], bool]


class _SeedEntry(NamedTuple):
    template_bytes: bytes
    dest_root: Path
    rel: Path
    policy: _SeedPolicy


class _PlannedAction(NamedTuple):
    verb: Literal["wrote", "overwrote", "preserved", "skipped", "unchanged", "removed"]
    kind: Literal["write", "remove_file", "remove_dir", "noop"]
    dest: Path
    display: str
    template_bytes: bytes | None
    report: bool = True


def _seed_policies(cwd: Path) -> dict[str, _SeedPolicy]:
    return {
        "application-pipeline": _SeedPolicy(
            bucket="application-pipeline",
            dest_root=cwd / "application-pipeline",
            operator_owned_roots=frozenset({"user-info"}),
            operator_owned_top_level_files=frozenset({"config.py", ".env"}),
            package_owned=_default_package_owned,
        ),
        "claude": _SeedPolicy(
            bucket="claude",
            dest_root=cwd / ".claude",
            operator_owned_roots=frozenset(),
            operator_owned_top_level_files=frozenset(),
            package_owned=_tool_skills_package_owned,
        ),
        "codex": _SeedPolicy(
            bucket="codex",
            dest_root=cwd / ".codex",
            operator_owned_roots=frozenset(),
            operator_owned_top_level_files=frozenset(),
            package_owned=_tool_skills_package_owned,
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
    seed_entries: list[_SeedEntry] = []
    application_pipeline_bucket = pkg / "application-pipeline"
    if application_pipeline_bucket.is_dir():
        seed_entries.extend(
            _collect_seed_entries(
                application_pipeline_bucket,
                policies["application-pipeline"].dest_root,
                Path(),
                policies["application-pipeline"],
            )
        )
        seed_entries.append(
            _SeedEntry(
                template_bytes=_OPERATOR_CREDENTIAL_PLACEHOLDER,
                dest_root=policies["application-pipeline"].dest_root,
                rel=Path(".env"),
                policy=policies["application-pipeline"],
            )
        )

    agent_skills_bucket = pkg / "agent-skills"
    if agent_skills_bucket.is_dir():
        for bucket_name in ("claude", "codex"):
            policy = policies[bucket_name]
            seed_entries.extend(
                _collect_seed_entries(
                    agent_skills_bucket,
                    policy.dest_root,
                    Path("skills"),
                    policy,
                )
            )

    actions = _plan_seed_actions(seed_entries, refresh=refresh)

    if refresh:
        actions.extend(_plan_refresh_cleanup_actions(policies))

    applied_actions = _apply_actions(actions)
    _render_report(applied_actions, refresh=refresh)


def _plan_refresh_cleanup_actions(
    policies: dict[str, _SeedPolicy],
) -> list[_PlannedAction]:
    actions: list[_PlannedAction] = []
    ap_root = policies["application-pipeline"].dest_root
    layout_path = ap_root / "layout.py"
    if layout_path.exists():
        actions.append(
            _PlannedAction("removed", "remove_file", layout_path, "layout.py", None)
        )
    actions.extend(_plan_retired_refresh_actions(policies))
    actions.extend(_plan_legacy_skills_cleanup_actions(ap_root))
    return actions


def _plan_legacy_skills_cleanup_actions(ap_root: Path) -> list[_PlannedAction]:
    actions: list[_PlannedAction] = []
    legacy_skills = ap_root / "skills"
    if not legacy_skills.is_dir():
        return actions
    legacy_skeleton = legacy_skills / "cv_skeleton.tex"
    if legacy_skeleton.exists():
        actions.append(
            _PlannedAction(
                "removed",
                "remove_file",
                legacy_skeleton,
                "skills/cv_skeleton.tex",
                None,
            )
        )

    remaining = [
        child.name
        for child in legacy_skills.iterdir()
        if child.name != legacy_skeleton.name or not legacy_skeleton.exists()
    ]
    if remaining:
        # User left other files inside — do not delete their content.
        return actions

    actions.append(
        _PlannedAction("removed", "remove_dir", legacy_skills, "skills/", None)
    )
    return actions


def _plan_retired_refresh_actions(
    policies: dict[str, _SeedPolicy],
) -> list[_PlannedAction]:
    actions: list[_PlannedAction] = []
    for bucket, rel_paths in _RETIRED_REFRESH_PATHS.items():
        root = policies[bucket].dest_root
        for rel in rel_paths:
            dest = root / rel
            if not dest.exists():
                continue
            actions.append(
                _PlannedAction(
                    "removed",
                    "remove_file",
                    dest,
                    _display_rel_path(rel, policy=policies[bucket]),
                    None,
                )
            )
            actions.extend(_plan_empty_parent_prune_actions(root, dest))
    return actions


def _plan_empty_parent_prune_actions(
    root: Path, removed_path: Path
) -> list[_PlannedAction]:
    actions: list[_PlannedAction] = []
    removed_child = removed_path.name
    node = removed_path.parent
    while node != root:
        if any(child.name != removed_child for child in node.iterdir()):
            return actions
        actions.append(
            _PlannedAction(
                "removed",
                "remove_dir",
                node,
                node.relative_to(root).as_posix(),
                None,
                False,
            )
        )
        removed_child = node.name
        node = node.parent
    return actions


def _collect_seed_entries(
    node: importlib.resources.abc.Traversable,
    target_dir: Path,
    rel: Path,
    policy: _SeedPolicy,
) -> list[_SeedEntry]:
    entries: list[_SeedEntry] = []
    for item in sorted(node.iterdir(), key=lambda i: i.name):
        if item.name.startswith("__"):
            continue
        item_rel = rel / item.name
        if item.is_dir():
            if len(rel.parts) == 0 and item.name in _EXCLUDE_ROOT_DIRS.get(
                policy.bucket, frozenset()
            ):
                continue
            entries.extend(_collect_seed_entries(item, target_dir, item_rel, policy))
            continue
        if len(rel.parts) == 0 and item.name in _EXCLUDE_FILES:
            continue
        entries.append(
            _SeedEntry(
                template_bytes=item.read_bytes(),
                dest_root=target_dir,
                rel=item_rel,
                policy=policy,
            )
        )
    return entries


def _plan_seed_actions(
    entries: list[_SeedEntry], *, refresh: bool
) -> list[_PlannedAction]:
    actions: list[_PlannedAction] = []
    for entry in entries:
        dest = entry.dest_root / entry.rel
        display = _display_rel_path(entry.rel, policy=entry.policy)
        package_owned = _is_package_owned(entry.rel, policy=entry.policy)
        overwrite = refresh and package_owned
        if dest.exists():
            if overwrite:
                if dest.read_bytes() != entry.template_bytes:
                    actions.append(
                        _PlannedAction(
                            "overwrote", "write", dest, display, entry.template_bytes
                        )
                    )
                else:
                    actions.append(
                        _PlannedAction("unchanged", "noop", dest, display, None)
                    )
            elif refresh:
                actions.append(_PlannedAction("preserved", "noop", dest, display, None))
            else:
                actions.append(_PlannedAction("skipped", "noop", dest, display, None))
            continue
        report = not refresh or package_owned
        actions.append(
            _PlannedAction(
                "wrote",
                "write",
                dest,
                display,
                entry.template_bytes,
                report,
            )
        )
    return actions


def _apply_actions(actions: list[_PlannedAction]) -> list[_PlannedAction]:
    applied_actions: list[_PlannedAction] = []
    for action in actions:
        if action.kind == "write":
            action.dest.parent.mkdir(parents=True, exist_ok=True)
            assert action.template_bytes is not None
            action.dest.write_bytes(action.template_bytes)
        elif action.kind == "remove_file":
            action.dest.unlink()
        elif action.kind == "remove_dir":
            action.dest.rmdir()
        applied_actions.append(action)
    return applied_actions


def _render_report(actions: list[_PlannedAction], *, refresh: bool) -> None:
    for line in _report_lines(actions, refresh=refresh):
        print(line)


def _report_lines(actions: list[_PlannedAction], *, refresh: bool) -> list[str]:
    if refresh:
        visible = [
            f"{action.verb} {action.display}"
            for action in actions
            if action.report and action.verb in ("wrote", "overwrote", "removed")
        ]
        if visible:
            return visible
        return ["directory is current — no files changed"]

    wrote = sum(1 for action in actions if action.report and action.verb == "wrote")
    skipped = sum(1 for action in actions if action.report and action.verb == "skipped")
    if wrote and skipped:
        return [f"wrote {wrote} files, skipped {skipped}"]
    if wrote:
        return [f"wrote {wrote} files"]
    return [f"skipped {skipped} files"]


def _is_package_owned(rel: Path, *, policy: _SeedPolicy) -> bool:
    """Return True for package-owned refresh artefacts; False for operator-owned ones."""
    parts = rel.parts
    if len(parts) == 1 and parts[0] in policy.operator_owned_top_level_files:
        return False
    if parts and parts[0] in policy.operator_owned_roots:
        return False
    return policy.package_owned(rel)


def _default_package_owned(rel: Path) -> bool:
    return True


def _tool_skills_package_owned(rel: Path) -> bool:
    parts = rel.parts
    if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
        return True
    if len(parts) >= 3 and parts[0] == "skills" and parts[1] == "_shared":
        return True
    return False


def _display_rel_path(rel: Path, *, policy: _SeedPolicy) -> str:
    rel_posix = rel.as_posix()
    if policy.bucket == "application-pipeline":
        return rel_posix
    return f".{policy.bucket}/{rel_posix}"

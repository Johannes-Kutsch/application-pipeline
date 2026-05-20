from __future__ import annotations

import importlib.resources
import shutil
import subprocess
import sys
from pathlib import Path

from application_pipeline.init_cmd import home_dir
from application_pipeline.latex import slot_map

_BUILDS = ("cover", "resume", "combined")

_LATEX_SUFFIXES = frozenset({".tex", ".cls", ".sty"})


def compile_cv(app_dir: Path) -> None:
    config_path = home_dir() / "config.py"
    if not config_path.exists():
        cwd = Path.cwd()
        print(
            f"no application-pipeline/config.py in {cwd}"
            " — did you forget to cd, or run init?",
            file=sys.stderr,
        )
        sys.exit(2)

    app_dir = app_dir.resolve()
    cv_tex = app_dir / "cv.tex"
    if not cv_tex.exists():
        print(
            f"no cv.tex in {app_dir} — did you forget to run /write-cv?",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        slots = slot_map.parse(cv_tex)
    except slot_map.SlotMapError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    user_info_dir = (home_dir() / "user-info").resolve()
    build_dir = app_dir / ".build"

    build_dir.mkdir(exist_ok=True)

    pkg = importlib.resources.files("application_pipeline.latex")
    template_text: str | None = None
    for item in pkg.iterdir():
        if Path(item.name).suffix not in _LATEX_SUFFIXES:
            continue
        if item.name == "cv_template.tex":
            template_text = item.read_text(encoding="utf-8")
        else:
            (build_dir / item.name).write_bytes(item.read_bytes())

    if template_text is None:
        raise FileNotFoundError("cv_template.tex not found in package")

    substituted = _substitute_slots(template_text, slots)
    (build_dir / "cv.tex").write_text(substituted, encoding="utf-8")

    for build_name in _BUILDS:
        tex_input = (
            rf"\def\UserDataDir{{{user_info_dir.as_posix()}}}"
            rf"\def\BUILD{{{build_name}}}"
            r"\input{cv}"
        )
        cmd = [
            "pdflatex",
            "-interaction=nonstopmode",
            "-jobname",
            build_name,
            tex_input,
        ]
        result = subprocess.run(cmd, cwd=build_dir, capture_output=True)

        if result.returncode != 0:
            log_file = build_dir / f"{build_name}.log"
            if log_file.exists():
                _emit_error_blob(log_file)
            sys.exit(1)

    # All three succeeded — move PDFs to app_dir and clean up .build/
    for build_name in _BUILDS:
        shutil.copy2(build_dir / f"{build_name}.pdf", app_dir / f"{build_name}.pdf")
    shutil.rmtree(build_dir)


def _substitute_slots(template: str, slots: dict[str, str]) -> str:
    result = template
    for name, body in slots.items():
        result = result.replace(f"<<{name.upper()}>>", body.rstrip("\n"))
    return result


def _emit_error_blob(log_file: Path) -> None:
    lines = log_file.read_text(errors="replace").splitlines()
    blob: list[str] = []
    trailing = 0

    for line in lines:
        if line.startswith("!"):
            blob.append(line)
            trailing = 5
        elif trailing > 0:
            blob.append(line)
            trailing -= 1

    if blob:
        print("\n".join(blob), file=sys.stderr)

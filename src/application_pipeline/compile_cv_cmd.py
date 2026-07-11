from __future__ import annotations

import importlib.resources
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from application_pipeline.compile_cv_local import (
    _CompileCvLocalProductionAdapter,
    _PdflatexAdapter,
)
from application_pipeline.cv_slot_contract import template_marker
from application_pipeline.init_cmd import home_dir, missing_config_message
from application_pipeline.latex import slot_map
from application_pipeline.project_order_optimizer import optimize_project_order

_BUILDS = ("cover", "resume", "combined")

_LATEX_SUFFIXES = frozenset({".tex", ".cls", ".sty"})

_PROJECT_MACRO_RE = re.compile(r"(?m)^\\([A-Za-z]\w*)\s*$")


def compile_cv(app_dir: Path) -> None:
    _CompileCvWorkflow(app_dir).run()


@dataclass(slots=True)
class _CompileCvWorkflow:
    app_dir: Path
    pdflatex: _PdflatexAdapter | None = None

    def run(self) -> None:
        self._require_config()
        app_dir = self.app_dir.resolve()
        slots = self._parse_slot_map(app_dir)
        if self.pdflatex is None:
            self.pdflatex = _CompileCvLocalProductionAdapter()
        build_dir = app_dir / ".build"
        build_dir.mkdir(exist_ok=True)
        template_text = self._stage_latex(build_dir, slots)
        winning_slots = self._probe_and_reorder(build_dir, template_text, slots)
        (build_dir / "cv.tex").write_text(
            _substitute_slots(template_text, winning_slots), encoding="utf-8"
        )
        self._run_builds(build_dir)
        self._publish_pdfs(build_dir, app_dir)
        shutil.rmtree(build_dir)

    def _require_config(self) -> None:
        config_path = home_dir() / "config.py"
        if config_path.exists():
            return
        print(missing_config_message(Path.cwd()), file=sys.stderr)
        sys.exit(2)

    def _parse_slot_map(self, app_dir: Path) -> dict[str, str]:
        cv_tex = app_dir / "cv.tex"
        if not cv_tex.exists():
            print(
                f"no cv.tex in {app_dir} — did you forget to run /write-cv?",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            return slot_map.parse(cv_tex)
        except slot_map.SlotMapError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    def _stage_latex(self, build_dir: Path, slots: dict[str, str]) -> str:
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
        return template_text

    def _probe_and_reorder(
        self,
        build_dir: Path,
        template_text: str,
        slots: dict[str, str],
    ) -> dict[str, str]:
        project_macros = _extract_project_macros(slots["resume_projekte"])
        if len(project_macros) <= 1:
            return slots

        assert self.pdflatex is not None
        cv_data_dir = (home_dir() / "user-info" / "cv").resolve()

        baseline = self.pdflatex.run_pass(
            build_dir=build_dir,
            build_name="resume",
            cv_data_dir=cv_data_dir,
        )
        if baseline.page_count is None or baseline.page_count <= 2:
            return slots

        for perm in optimize_project_order(project_macros):
            new_slots = dict(slots)
            new_slots["resume_projekte"] = _reorder_projekte_body(
                slots["resume_projekte"], perm
            )
            (build_dir / "cv.tex").write_text(
                _substitute_slots(template_text, new_slots), encoding="utf-8"
            )
            result = self.pdflatex.run_pass(
                build_dir=build_dir,
                build_name="resume",
                cv_data_dir=cv_data_dir,
            )
            if result.page_count is not None and result.page_count <= 2:
                new_order_str = " ".join(f"\\{m}" for m in perm)
                orig_order_str = " ".join(f"\\{m}" for m in project_macros)
                print(f"reorder: {new_order_str} (was: {orig_order_str})")
                return new_slots

        print(
            "warning: no project order reduces resume to ≤2 pages;"
            " using original order",
            file=sys.stderr,
        )
        return slots

    def _run_builds(self, build_dir: Path) -> None:
        assert self.pdflatex is not None
        cv_data_dir = (home_dir() / "user-info" / "cv").resolve()
        for build_name in _BUILDS:
            # Two passes: first writes \label{lastpage} to .aux; second lets
            # moderncv.cls's AtBeginDocument hook read \pageref{lastpage} and emit
            # page numbers in the right footer.
            for _ in range(2):
                result = self.pdflatex.run_pass(
                    build_dir=build_dir,
                    build_name=build_name,
                    cv_data_dir=cv_data_dir,
                )
                if result.returncode == 0:
                    continue
                log_file = build_dir / f"{build_name}.log"
                if result.log_text is not None:
                    _emit_error_blob(result.log_text)
                elif log_file.exists():
                    _emit_error_blob(log_file.read_text(errors="replace"))
                sys.exit(1)

    def _publish_pdfs(self, build_dir: Path, app_dir: Path) -> None:
        app_suffix = app_dir.name
        for build_name in _BUILDS:
            suffixed_pdf = app_dir / f"{build_name}_{app_suffix}.pdf"
            shutil.copy2(build_dir / f"{build_name}.pdf", suffixed_pdf)
            generic_pdf = app_dir / f"{build_name}.pdf"
            if generic_pdf.exists():
                generic_pdf.unlink()


def _extract_project_macros(body: str) -> list[str]:
    return _PROJECT_MACRO_RE.findall(body)


def _reorder_projekte_body(original_body: str, new_order: list[str]) -> str:
    trailing = original_body.endswith("\n")
    return "\n".join(f"\\{name}" for name in new_order) + ("\n" if trailing else "")


def _substitute_slots(template: str, slots: dict[str, str]) -> str:
    result = template
    for name, body in slots.items():
        result = result.replace(template_marker(name), body.rstrip("\n"))
    return result


def _emit_error_blob(log_text: str) -> None:
    lines = log_text.splitlines()
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

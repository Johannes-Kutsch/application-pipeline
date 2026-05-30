# Build-Command-Vertrag

<build-contract>
Aufruf: `application-pipeline compile-cv <application-folder>`.

**Precondition:** vor dem Aufruf verifizieren, dass das `application-pipeline`-CLI auf PATH ist. Bei Miss: dem User sagen, dass das `application-pipeline`-Package installiert bzw. das passende venv aktiviert werden muss, dann stopp — kein Build-Versuch.

- **Erfolg** → `cover.pdf`, `resume.pdf`, `combined.pdf` landen im Application-Ordner; das Command räumt sein `.build/`-Working-Dir auf und beendet mit Exit-Code 0.
- **Fehler** → das Command schreibt einen Grep der `!`-Stanzas + ~5 Zeilen Trailing-Kontext aus dem fehlgeschlagenen Job-Log nach stderr und beendet mit Non-Zero.

Skills kennen `pdflatex`, Jobnames, `\BUILD` oder Aux-Files nicht — das alles lebt im Command (`application_pipeline/compile_cv_cmd.py`).
</build-contract>

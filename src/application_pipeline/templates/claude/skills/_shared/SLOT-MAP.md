# CV Slot-Map

<slot-map>
Eine **CV Slot-Map** ist eine `.tex`-Datei, die ausschließlich aus Slot-Headern und Bodies besteht — keine LaTeX-Präambel, kein `\documentclass`, kein `\input{cv_template}`. Spec laut ADR-0030.

**Header-Form:** `^%% SLOT: <slot_name>$` (case-sensitive, exakt diese Form).

**Body:** läuft vom Slot-Header bis zum nächsten `%% SLOT:`-Header oder EOF. Bodies sind **rohe TeX-Fragmente** — keine Escape-Regeln, keine Umlaut-Substitution. `\href{...}{...}`, `\textit{...}`, Umlaute (ä, ö, ü, ß, Ä, Ö, Ü), Em-Dashes (`---`) werden verbatim verwendet.

**Source-of-Truth für die Slot-Liste:** `application-pipeline/skills/cv_skeleton.tex`. Jede Slot-Map (`cv.tex`, Skeleton selbst) muss exakt dieselben Slot-Namen in derselben Reihenfolge enthalten — keine zusätzlichen, keine fehlenden, keine umbenannten. Beim Start: Skeleton parsen, dann gegen `cv.tex` vergleichen.

**Shape ist read-only:** Slot-Liste, Reihenfolge und Header-Form sind nicht editierbar. Edits passieren ausschließlich an Slot-Bodies.

**One-Line-Slots:** Slots, die im Skeleton mit einer einzigen Body-Zeile auskommen (z.B. die DIN-5008-Recipient-Slots, `opening`), tragen pro Slot genau **eine** Information. Keine `\\`-Zeilenumbrüche im Body — Zeilenstruktur erzeugt das Template.
</slot-map>

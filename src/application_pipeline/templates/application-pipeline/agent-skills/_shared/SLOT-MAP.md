<slot-map>
Eine **CV Slot-Map** ist eine `.tex`-Datei, die ausschließlich aus Slot-Headern und Bodies besteht — keine LaTeX-Präambel, kein `\documentclass`, kein `\input{cv_template}`.

*Source-of-Truth für die Slot-Liste:** `application-pipeline/cv-template/cv_skeleton.tex`. Jede Slot-Map muss exakt dieselben Slot-Namen in derselben Reihenfolge enthalten. Beim Start: Skeleton parsen, dann gegen `cv.tex` vergleichen.

**Header-Form:** `^%% SLOT: <slot_name>$` (case-sensitive, exakt diese Form).

**Body:** läuft vom Slot-Header bis zum nächsten `%% SLOT:`-Header oder EOF. Bodies sind **rohe TeX-Fragmente** — keine Escape-Regeln, keine Umlaut-Substitution. `\href{...}{...}`, `\textit{...}`, Umlaute (ä, ö, ü, ß, Ä, Ö, Ü), Em-Dashes (`---`) werden verbatim verwendet.

**Shape ist read-only:** Slot-Liste, Reihenfolge und Header-Form sind nicht editierbar. Edits passieren ausschließlich an Slot-Bodies.

**One-Line-Slots:** Slots, die im Skeleton mit einer einzigen Body-Zeile auskommen, tragen pro Slot genau **eine** Information.
</slot-map>

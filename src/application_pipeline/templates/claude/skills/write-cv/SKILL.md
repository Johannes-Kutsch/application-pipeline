---
name: write-cv
description: Generates a tailored cv.tex (CV Slot-Map) plus cover/resume/combined PDFs for a listing previously analysed by /analyse-listing. Calls `application-pipeline compile-cv` and iteratively strips content until cover ≤ 1 page and resume ≤ 2 pages. Runs when the user types /write-cv.
---

## Startup-Checks

Falls `<application-folder>/cv.tex` bereits exitiert, frage den User ob er die Datei neu generieren oder einfach nur die pdf's builden möchte.

Führe die Checks aus [../_shared/STARTUP-APPLICATION.md](../_shared/STARTUP-APPLICATION.md) aus.

# /write-cv

Universalregeln: [../_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md).

## Argumente

Siehe [../_shared/APPLICATION-FOLDER-ARG.md](../_shared/APPLICATION-FOLDER-ARG.md).

## Inputs einlesen

Lies alle Inputs in den Speicher:

- `analysis.md` — Raw Description + „Why apply"-Bullets + Listing-fordert / Hook / Anekdote-Tailoring-Hooks.
- `application-pipeline/user-info/triage-profile/*.md` — Identität + Match-Kriterien. Geladen: `candidate-profile.md`.
- `application-pipeline/user-info/cv/writing-style.md` — Phrasing-Regeln und Cover-Strategie.
- `application-pipeline/user-info/cv/positive-exemplars.md` — Stil-Vorbilder (vier Vorbild-Briefe).
- `application-pipeline/user-info/cv/content_pool.tex` — jeder `%%% ITEM: …`-Block mit den drei Feldern `always`, `group` (optional), `relevance`. Section wird aus der nächstgelegenen vorausgehenden `% ===== <name> =====`-Blocküberschrift abgeleitet. **Die `\newcommand`-Bodies werden roh als TeX in den Prompt aufgenommen** — kein Stripper, keine Escape-Regeln.
- `application-pipeline/user-info/triage-profile/skills.md` — **Skills-Pool** laut ADR-0033. H2-Headings sind Skill-Gruppen (Heading-Text = `\cvitem{<group>}{...}`-Kategorie-Label), `-`-Bullets sind Skill-Namen. Optionaler pandoc-style `{...}`-Attributblock am Zeilenende: Gruppen tragen `always` (bare) und `<jobtype>=<high|medium|low>`-Relevanz-Einträge; Items tragen nur `always`. Bullets vor der ersten H2 werden ignoriert (`/write-cv` rendert nur gruppierte Skills). Unbekannte Attribute werden ignoriert. Source-of-Truth — keine Skill-Namen aus anderen Quellen.
- `application-pipeline/cv-template/cv_skeleton.tex` — das **CV-Skelett**. Diese Datei ist der Format-by-Example und die Source-of-Truth für die Slot-Liste. Jeder `%% SLOT: <name>`-Block enthält Prompt-Guidance-Kommentare (`% …`-Zeilen direkt nach dem Slot-Header) plus einen Beispiel-Body. Beide werden ausgewertet.

## CV Slot-Map entwerfen

Format-Spec, Header-Form, Body-Semantik und Slot-Listen-Source-of-Truth: siehe [../_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md).

**Pro Slot:**

- Die Prompt-Guidance-Kommentare unmittelbar nach dem `%% SLOT:`-Header (alle `% …`-Zeilen bis zum ersten Nicht-Kommentar-Inhalt) sind verbindliche Anweisungen für diesen Slot. Halte dich daran (Tonfall, Längenhinweise, was rein darf / was nicht).
- Der Beispiel-Body im Skelett ist Format-by-Example: Stil, TeX-Konstrukte (`\href`, `\textit`, `\cventry`, …) und Mehrzeiligkeit zeigen, wie der echte Body aussehen soll. Nicht den Beispiel-Body wörtlich kopieren.
- Die Prompt-Guidance-Kommentare aus dem Skelett werden **nicht** in `cv.tex` übernommen — die emittierte Datei enthält nur Slot-Header und Body.

**Cover-Paragraph-Slots (`cover_intro`, `cover_pivot`, `cover_fit`, `cover_closing`) und `opening`:** Stimme und Strategie laut `cv/writing-style.md` (inkl. Sektion `## Cover-Strategie`) und `cv/positive-exemplars.md`. Geerdet in den „Why apply"-Bullets aus `analysis.md` und den Tailoring-Hooks. Erfinde **keine** Fakten; jede Behauptung lässt sich auf `analysis.md` oder eine Triage-Profil-Datei zurückführen.

**Recipient-Slots:** Empfänger-Infos aus `analysis.md` ziehen. Slot-Semantik komplett laut Skeleton-Guidance.

**Resume-Slots (`resume_berufserfahrung`, `resume_ausbildung`, `resume_projekte`):** Content-Pool-Items, gewählt per untenstehender Auswahlregel, als Folge von `\<itemName>`-Macro-Aufrufen im Body. Die Macros sind in `content_pool.tex` definiert; `cv_template.tex` bindet das per `\input` ein.

**Skills-Block (`skills_block`):** wird **mechanisch** aus `user-info/triage-profile/skills.md` zusammengesetzt — siehe „Skills-Block-Assembly" unten. Keine freie Authoring-Hand, keine Skill-Namen außerhalb des Pools.

## Content-Pool-Auswahl

Für die drei Resume-Slots:

1. **`always: true`-Items** werden immer aufgenommen — vorbehaltlich der `group:`-Exklusivität.
2. **`group:`-Exklusivität.** Items mit demselben `group:`-Wert sind alternative Varianten desselben Eintrags und schließen sich gegenseitig aus — pro `group:` darf höchstens **ein** Item in einem Resume-Slot landen. Wenn beide Varianten `always: true` tragen, gilt die `group:`-Exklusivität trotzdem: genau eine wird gewählt, nie beide.
3. **Restliche Items** (ohne `always: true`, ohne `group:`-Konflikt): match das jeweilige Item (Rohbody + `relevance`) per LLM-Judgment gegen die Listing-Hooks aus `analysis.md`. Wähle im ersten Durchgang großzügig — Overflow behandelt der Strip-Down-Loop.
4. **Section-Routing.** Jedes gewählte Item wird in den Resume-Slot geschrieben, dessen Name der Section seines `% ===== <name> =====`-Blockheaders entspricht (`Berufserfahrung` → `resume_berufserfahrung`, `Ausbildung` → `resume_ausbildung`, `Projekte` → `resume_projekte`).
5. **Innerhalb jedes Resume-Slots:** relevanteste zuerst.

Items werden als reine `\<itemName>`-Macro-Aufrufe in den Body geschrieben, eine pro Zeile.

## Skills-Block-Assembly

Der `skills_block`-Slot wird mechanisch aus dem Skills-Pool zusammengesetzt — die LLM-Rolle ist ausschließlich *Auswahl*, niemals *Erfindung* von Skill-Namen. Algorithmus laut ADR-0033:

1. **Pool parsen.** Lies `application-pipeline/user-info/triage-profile/skills.md` und parse die Gruppen + Items + Attribute strikt nach der ADR-0033-Grammatik (H2 = Gruppe, Bullet = Item, `{...}`-Attributblock optional am Zeilenende). Gruppen-Attribute: `always` (bare) und `<jobtype>=<high|medium|low>` Relevanzen. Item-Attribut: `always`. Unbekannte Tokens stillschweigend ignorieren. Bullets vor der ersten H2 verwerfen.
2. **Jobtype aus dem Listing ableiten.** Bestimme den Jobtype des aktuellen Listings (z.B. `mle`, `games`, `agents`) per LLM-Judgment aus `analysis.md` (raw_description + Tailoring-Hooks). Der Jobtype-Schlüssel entspricht den Relevanz-Keys im Pool — wähle deine Bezeichnung passend zu den vorhandenen Keys.
3. **Gruppen-Auswahl:**
   - Gruppen mit `{always}` werden **immer** in die Auswahl aufgenommen — unabhängig vom Jobtype.
   - Non-always-Gruppen: wähle null oder mehr per LLM-Judgment, basierend auf ihren Relevanz-Einträgen gegen den Listing-Jobtype (`high` ≫ `medium` ≫ `low`; Gruppen ohne passenden Relevanz-Eintrag dürfen weggelassen werden).
   - Eine Gruppe, die nicht in die Auswahl kommt, trägt nichts bei — auch wenn sie `{always}`-Items enthält. Item-`always` befördert seine Gruppe **nicht**.
4. **Item-Auswahl pro gewählter Gruppe:**
   - Alle Items mit `{always}` werden bedingungslos aufgenommen.
   - Non-always-Items: wähle null oder mehr per LLM-Judgment gegen die Listing-Hooks.
5. **Validierung der LLM-Picks.** Jeder gewählte Item-Name muss verbatim im geparsten Pool für diese Gruppe vorkommen. LLM-Picks außerhalb des Pools werden verworfen (kurzer `parser_log`-style Warnhinweis im finalen Erfolgs-Report).
6. **Render-Reihenfolge:** File-Order von `skills.md` — sowohl über Gruppen hinweg als auch über Items innerhalb einer Gruppe.
7. **Leere Gruppen kollabieren.** Eine gewählte Gruppe ohne `always`-Items und ohne LLM-Picks erzeugt **keine** `\cvitem`-Zeile.
8. **Emit.** Pro nicht-leerer gewählter Gruppe genau **eine** Zeile in den Body von `%% SLOT: skills_block`:

   ```
   \cvitem{<heading-text>}{<skill1>, <skill2>, ...}
   ```

   `<heading-text>` ist der H2-Text der Gruppe verbatim. Skill-Namen kommagetrennt in File-Order. Keine weiteren TeX-Konstrukte, keine `\textit`/Klammer-Annotations, keine freihändigen Skills.

## `cv.tex` schreiben

Schreibe die zusammengesetzte Slot-Map nach `<application-folder>/cv.tex`. Der Build-Pfad substituiert die Bodies in `cv_template.tex` (das im Package liegt).

## Build-Aufruf

Rufe das Build-Skript laut [../_shared/BUILD-CONTRACT.md](../_shared/BUILD-CONTRACT.md) auf.

Bei Non-Zero-Exit: dem User in Prosa sagen, dass der Compile fehlgeschlagen ist, mit dem stderr verbatim als Anhang. Dann stopp. Versuche den Strip-Down-Loop nicht — der behandelt nur Overflow, keine syntaktischen LaTeX-Fehler und keine Slot-Map-Parser-Fehler (fehlende/extra Slots).

## Seiten-Overflow-Strip-Down-Loop

Nach erfolgreichem Build: Seitenzahlen von `cover.pdf` und `resume.pdf` lesen und den Loop laut [../_shared/STRIP-DOWN.md](../_shared/STRIP-DOWN.md) fahren.

## Erfolgs-Report

Wenn der Loop konvergiert: in Prosa eine kurze Zusammenfassung — Pfad zum Application-Ordner, die drei PDF-Dateinamen mit finalen Seitenzahlen, Anzahl Strip-Down-Iterationen, und ein vorgeschlagener `/iterate-cv`-Aufruf mit **vollqualifiziertem** `application-pipeline/applications/<folder>/`-Pfad (damit der User copy-pasten kann).

## Schreib-Whitelist

<hard-rules>
Dieser Skill schreibt ausschließlich in:

- `<application-folder>/cv.tex` (Format: Slot-Map laut [../_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md))

`cover.pdf`, `resume.pdf`, `combined.pdf` werden vom `compile-cv`-Command geschrieben, nicht vom Skill. Alles andere im Repo ist read-only.
</hard-rules>

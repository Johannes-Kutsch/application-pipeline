# Startup-Checks

Falls `<application-folder>/cv.tex` bereits existiert, frage den User, ob er die Datei neu generieren, nur die PDFs bauen oder direkt den bestehenden Draft iterieren möchte.

# Universalregeln

[_shared/CONVENTIONS.md](application-pipeline/agent-skills/_shared/CONVENTIONS.md)

# Aufgabe

Erstelle eine `cv.tex`-Datei im `<application-folder>` und fülle sie mit Content.

## CV Slot-Map entwerfen

[_shared/SLOT-MAP.md](application-pipeline/agent-skills/_shared/SLOT-MAP.md)

Erstelle als erstes eine leere Slotmap in `<application-folder>/cv.tex`, indem du `application-pipeline/cv-template/cv_skeleton.tex` kopierst.

## 1. Adresse und Anrede ausfüllen

1. Lies die Datei `<application-folder>/analysis.md`.
2. Analysiere die Stellenausschreibung und fülle die Slots `recipient_company`, `recipient_name`, `recipient_street`, `recipient_zip_city` und `opening` aus.
3. Falls Informationen fehlen, frage den Nutzer.
4. Schreibe die Informationen jetzt in `<application-folder>/cv.tex`.

## 2. Anschreibenstext erstellen

- Ziel dieses Schrittes ist es die 4 Absatz-Slots des Anschreibens zu erstellen.
- Lies die Datei `application-pipeline/user-info/cv/cover-patterns.md`. Für jeden Absatz im Anschreiben gibt es hier vorformulierte Texte.
- Die Absätze `cover_intro` und 'cover_closing' sind generisch und können direkt angepasst aus `cover-patterns.md` nach cv.tex übernommen werden
- Für die erstellung der Absätz `cover_pivot` und `cover_fit` gehe nacheinander in einen Grilling-Loop mit dem Nutzer. Dabei gilt:
  - Schreibe die Umlaute ä, ü, ö und ß genau so.
  - Kein Text für `cover_pivot` und `cover_fit` darf in cv.tex geschrieben werden, bevor der jeweilige Absatz vom User explizit freigegeben wurde.
  - pro Absatz: Vorschlag präsentieren -> auf Antwort warten -> nur bei expliziter Zustimmung schreiben -> erst dann nächster Absatz

### `cover_pivot` und `cover_fit` Absatz Loop

Gehe nacheinander für jeden Absatz diese Grilling-Loop durch

<per-absatz-flow>
1. Analysiere `analysis.md` und bestimme Slot-Zweck und passenden `argument_type`.
2. Suche in `cover-patterns.md` nach einem passenden Pattern.
3. Präsentiere dem User:
   - bei klarem Match genau einen Vorschlag,
   - sonst genau drei Alternativen.
4. Frage explizit nach Freigabe.
5. Schreibe den Text erst dann in `cv.tex`, wenn der User den konkreten Absatz ausdrücklich freigegeben hat. Wenn keine explizite Freigabe vorliegt, darf kein Schreibschritt erfolgen und es dürfen keine weiteren Cover-Absätze bearbeitet werden.
6. Wenn der User ablehnt oder umformuliert, wiederhole den Vorschlagsprozess.
7. Wenn es keinen klaren Match gab, übernehme die neue Formulierung nach `cover-patterns.md`. Änderungen oder neue Formulierungen dürfen nur nach Freigabe in `cover-patterns.md` übernommen werden.
</per-absatz-flow>

## 4. Resume Slots füllen

Ziel ist es, das Resume mit an die Analyse angepasstem Inhalt zu füllen.

### 4.1. Makro Flow

Ziel ist es, die Slots Berufserfahrung, Ausbildung und Projekte mit Makros zu füllen.

- Lies die Datei `application-pipeline/user-info/cv/content_pool.tex`. Hier sind Makros für Berufserfahrung, Ausbildung und Projekte hinterlegt.
- Lies die Datei `application-pipeline/user-info/triage-profile/candidate-profile.md`. Hier sind Identität plus Match-Kriterien hinterlegt.

<Makro-Slot-Regeln>
- `always: true`-Items werden immer aufgenommen - vorbehaltlich der `group:`-Exklusivität.
- Items mit demselben `group:`-Wert sind alternative Varianten desselben Eintrags und schließen sich gegenseitig aus.
- Restliche Items (ohne `always: true`, ohne `group:`-Konflikt): match das jeweilige Item gegen `analysis.md`.
- Innerhalb jedes Resume-Slots: relevanteste zuerst.
- Items werden als reine `\itemName`-Macro-Aufrufe in den Body geschrieben, eines pro Zeile.
</Makro-Slot-Regeln>

### 4.2. Skills

- Lies `application-pipeline/user-info/triage-profile/skills.md` und parse Gruppen, Items und Attribute.

Der `skills_block`-Slot wird mechanisch aus dem Skills-Pool zusammengesetzt. Die LLM-Rolle ist ausschließlich Auswahl, niemals Erfindung von Skill-Namen.

1. Pool parsen: Lies `application-pipeline/user-info/triage-profile/skills.md`.
2. Gruppen-Auswahl: `always`-Gruppen immer aufnehmen, weitere Gruppen nach Relevanz und Judgment.
3. Item-Auswahl pro gewählter Gruppe: `always`-Items immer aufnehmen, weitere nach Judgment.
4. Gültigkeit der Picks prüfen: Jeder gewählte Item-Name muss wörtlich im geparsten Pool vorkommen.
5. Emit: pro nicht-leerer gewählter Gruppe genau eine Zeile in `%% SLOT: skills_block`:

<skille-gruppen-zeile>
\cvitem{<heading-text>}{<skill1>, <skill2>, ...}
</skille-gruppen-zeile>

## 5. Build-Aufruf

Build erst nach vollständig freigegebenen Cover-Absätzen und finaler Slot-Map.
Rufe das Build-Skript `application-pipeline compile-cv <application-folder>` auf. Erfolg: `cover.pdf`, `resume.pdf` und `combined.pdf` landen im Application-Ordner. Bei Non-Zero-Exit: dem User sagen, dass der Compile fehlgeschlagen ist, mit dem `stderr` verbatim als Anhang. Dann stoppen.

## 6. Seiten-Overflow-Loop

Nach erfolgreichem Build: Seitenzahlen von `cover.pdf` und `resume.pdf` lesen. `cover.pdf` darf nur eine Seite, `resume.pdf` maximal zwei Seiten lang sein.

- Resume-Overflow bleibt automatisch und läuft nach dem untenstehenden Strip-Down-Loop.
- Cover-Overflow geht in Interactive Cover Shortening statt in einen rein automatischen Strip-Down.

### 6.1. Interactive Cover Shortening

Wenn `cover.pdf` zu lang ist:

<cover-shortening-loop>
1. Erzeuge für jeden Absatz eine verkürzte Variante.
2. Zeige dem User die verkürzten Varianten vollständig in Prosa.
3. Lass den User genau eine oder mehrere Varianten auswählen.
4. Schreibe nur die vom User gewählten verkürzten Varianten nach `<application-folder>/cv.tex`. Lasse alle anderen Slots unverändert.
5. Baue erneut und prüfe die Seitenzahl neu.
6. Wiederhole mit Schritt 1, falls weiterhin Cover-Overflow besteht.
</cover-shortening-loop>

Wichtig: Dieser Post-Build-Shortening-Loop schreibt nie nach `cover-patterns.md`.

### 6.2. Automatic Resume Shortening

Wenn `resume.pdf` zu lang ist:

<resume-shortening-loop>
1. Erzeuge für jeden Resume-Slot eine verkürzte Variante.
2. Wähle die beste Kürzung aus.
3. Schreibe nur diese verkürzte Variante nach `<application-folder>/cv.tex`. Lasse alle anderen Slots unverändert.
4. Baue erneut und prüfe die Seitenzahl neu.
5. Wiederhole mit Schritt 1, falls weiterhin Resume-Overflow besteht.
</resume-shortening-loop>

## 7. Erfolgs-Report

Wenn der Loop konvergiert: in Prosa eine kurze Zusammenfassung - Pfad zum Application-Ordner, die drei generierten PDFs mit finalen Seitenzahlen und der Text des Anschreibens.

### Schreib-Whitelist

Dieser Skill schreibt ausschließlich in:

- `<application-folder>/cv.tex`
- `application-pipeline/user-info/cv/cover-patterns.md`
- `<application-folder>/analysis.md`
- `application-pipeline/user-info/triage-profile/*.md`

`cover.pdf`, `resume.pdf`, `combined.pdf` werden vom `compile-cv`-Command geschrieben, nicht vom Skill. Alles andere im Repo ist read-only.

---
name: write-cv
description: Erzeugt eine angepasste cv.tex (CV Slot-Map) plus anwendungsgebundene cover/resume/combined PDFs fuer ein durch /analyse-listing analysiertes Listing. Haltet einen editierbaren Feedback-Loop fuer cv.tex, Build-Output und triage-profile bis der Nutzer beendet.
---

# /write-cv

# Aufgabe

Erstelle eine `cv.tex`-Datei im `<application-folder>` und fülle sie mit Inhalt.

# Universalregeln

[_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md)

<write-rules>
Dieser Skill schreibt ausschließlich in:

- `<application-folder>/cv.tex`
- `application-pipeline/user-info/cv/cover-patterns.md`
</write-rules>

## 1. Ordner erstellen

Extrahiere `Company` und `Role` aus dem gewählten Listing durch eigenes Lesen. Das Ordnerdatum ist immer **heute**, unabhängig vom `posted_date` im Text.

Baue den Ordner-Bezeichner nach folgenden Regeln:

<slug-rules>
1. Entferne Gender-Marker: `(m/w/d)`, `(m/f/d)`, `(d/w/m)`, `(w/m/d)` und Äquivalente.
2. Entferne Ortsanhänge am Ende: alles nach dem letzten `-` / `--` / ` - `, falls der Rest wie ein Ort aussieht.
3. Transliteriere deutsche Umlaute: `ae`, `oe`, `ue`, `ss` und Großbuchstaben-Varianten.
4. Ersetze jeden Lauf von Non-`[A-Za-z0-9]` durch ein einzelnes `-`.
5. Entferne führende und abschließende `-`.
6. Kürze auf 40 Zeichen (an der letzten `-` vor dem Limit abschneiden).
7. Großschreibung beibehalten.
</slug-rules>

Bestätige Company, Role und den resultierenden Pfad (`application-pipeline/applications/<today>-<Company-slug>_<Role-slug>/`) mit dem Nutzer. Erstelle anschließend einen leeren Ordner mit diesem Pfad.


## 2. CV Slot-Map entwerfen

[_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md)

Erstelle als erstes eine leere Slotmap in `<application-folder>/cv.tex`, indem du `application-pipeline/cv-template/cv_skeleton.tex` kopierst.

## 3. Adresse und Anrede ausfüllen

1. Analysiere die Stellenausschreibung und fülle die Slots `recipient_company`, `recipient_name`, `recipient_street`, `recipient_zip_city` und `opening` aus.
2. Falls Informationen fehlen, frage den Nutzer.

## 4. Anschreibenstext erstellen

- Ziel dieses Schritts ist es, die 4 Absatz-Slots des Anschreibens zu erstellen.
- Lies die Datei `application-pipeline/user-info/cv/cover-patterns.md`. Für jeden Absatz im Anschreiben gibt es hier vorformulierte Texte.
- Die Absätze `cover_intro` und `cover_closing` sind generisch und können direkt aus `cover-patterns.md` in `cv.tex` übernommen werden.
- Für die Erstellung der Absätze `cover_pivot` und `cover_fit` gehe nacheinander in einen Abstimmungsdurchlauf mit dem Nutzer. Dabei gilt:
  - Schreibe die Umlaute ä, ü, ö und ß genau so.
  - Kein Text für `cover_pivot` und `cover_fit` darf in `cv.tex` geschrieben werden, bevor der jeweilige Absatz vom Nutzer explizit freigegeben wurde.
  - Pro Absatz: Vorschlag präsentieren → auf Antwort warten → nur bei expliziter Zustimmung schreiben → dann nächster Absatz.

### `cover_pivot` und `cover_fit` Absatzdurchlauf

Gehe für `cover_pivot` und `cover_fit` nacheinander diesen Abstimmungsdurchlauf durch:

<per-absatz-flow>
1. Analysiere die Analyseergebnisse aus `/analyse-listing` und bestimme Slot-Zweck sowie passendes `argument_type`.
2. Suche in `cover-patterns.md` nach einem passenden Muster.
3. Präsentiere dem Nutzer:
   3.1. bei klarem Treffer genau einen Vorschlag,
   3.2. sonst drei unterschiedliche Alternativen.
4. Schreibe den Text erst dann in `cv.tex`, wenn der Nutzer den konkreten Absatz ausdrücklich freigegeben hat.
5. Wenn der Nutzer ablehnt oder umformuliert, wiederhole den Vorschlagsprozess ab Schritt 3.2.
6. Wenn eine neue Formulierung entsteht, übernehme sie nach freigabe in `cover-patterns.md`.
</per-absatz-flow>

## 5. Resume Slots füllen

Ziel dieses Schritts ist es, das Resume mit auf die Analyse angepasstem Inhalt zu füllen.

### 5.1. Makro-Ablauf

Ziel ist es, die Slots Berufserfahrung, Ausbildung und Projekte mit Makros zu füllen.

- Lies die Datei `application-pipeline/user-info/cv/content_pool.tex`. Hier sind Makros für Berufserfahrung, Ausbildung und Projekte hinterlegt.
- Lies die Datei `application-pipeline/user-info/triage-profile/candidate-profile.md`. Hier sind Identität plus Match-Kriterien hinterlegt.

<Makro-Slot-Regeln>
- `always: true`-Einträge werden immer aufgenommen – vorbehaltlich der `group:`-Exklusivität.
- Einträge mit demselben `group:`-Wert sind alternative Varianten desselben Eintrags und schließen sich gegenseitig aus.
- Restliche Einträge (ohne `always: true`, ohne `group:`-Konflikt): gleiche jeden Eintrag gegen die Analyseergebnisse aus `/analyse-listing` ab.
- Innerhalb jedes Resume-Slots: relevanteste zuerst.
- Einträge werden als reine `\itemName`-Macro-Aufrufe in den Body geschrieben, einer pro Zeile.
</Makro-Slot-Regeln>

### 5.2. Skills

- Lies `application-pipeline/user-info/triage-profile/skills.md` und analysiere Gruppen, Einträge und Attribute.

Der `skills_block`-Slot wird mechanisch aus dem Skill-Pool zusammengesetzt. Die LLM-Rolle besteht ausschließlich in der Auswahl, niemals in der Erfindung von Skill-Namen.

1. Pool einlesen: Lies `application-pipeline/user-info/triage-profile/skills.md`.
2. Gruppenauswahl: `always`-Gruppen immer aufnehmen, weitere Gruppen nach Relevanz und fachlicher Einschätzung wählen.
3. Eintragsauswahl pro gewählter Gruppe: `always`-Einträge immer aufnehmen, weitere nach fachlicher Einschätzung wählen.
4. Gültigkeit der Auswahl prüfen: Jeder gewählte Eintragsname muss exakt im eingelesenen Pool vorkommen.
5. Schreiben: Für jede nicht-leere gewählte Gruppe genau eine Zeile in `%% SLOT: skills_block`:

<skille-gruppen-zeile>
\cvitem{<heading-text>}{<skill1>, <skill2>, ...}
</skille-gruppen-zeile>

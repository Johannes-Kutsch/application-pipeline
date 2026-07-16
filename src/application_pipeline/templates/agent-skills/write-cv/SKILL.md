---
name: write-cv
description: Erstellt eine angepasste cv.tex (CV Slot-Map) plus Cover-, Resume- und Combined-PDFs für ein Listing, dessen 4 Bullets zuvor in dieser Session mit /analyse-listing finalisiert wurden.
disable-model-invocation: true
---

# Universalregeln

[_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md)

<write-rules>
Dieser Skill schreibt ausschließlich in:

- `<application-folder>/cv.tex`
- `application-pipeline/user-info/cv/cover-patterns.md`
</write-rules>

# Aufgabe

## 1. Ordner erstellen

Extrahiere `Company` und `Role` aus dem Listing. Das Ordnerdatum ist immer **heute**, unabhängig vom `posted_date` im Text.

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

Erstelle eine leere Slotmap in `<application-folder>/cv.tex`, indem du `application-pipeline/cv-template/cv_skeleton.tex` kopierst.

## 3. Adresse, Anrede und Betreff ausfüllen

1. Analysiere die Stellenausschreibung und fülle die Slots `recipient_company`, `recipient_name`, `recipient_street`, `recipient_zip_city` und `opening` aus.
2. Fülle `cover_subject`: `Betreff: Ihre Stellenanzeige \enquote{<Jobtitel>}` — nutze `\enquote{...}` statt gerader Anführungszeichen für den Jobtitel. Hänge ` Refnr. <Nummer>` an, falls die Ausschreibung eine Referenznummer enthält, sonst weglassen.
3. Falls Informationen fehlen, frage den Nutzer.

## 4. Anschreibenstext erstellen

Die Absätze `cover_intro` und `cover_closing` sind generisch und werden wortgenau aus `application-pipeline/user-info/cv/cover-patterns.md` übernommen. Passe nur listing-spezifische Informationen (z. B. Jobtitel) an.

Der Slot `cover_bullets` enthält eine Intro-Zeile gefolgt von einer Stichpunktliste:

1. Lies die Intro-Zeile aus `application-pipeline/user-info/cv/bullet-library.md` und übernimm sie wortgenau.
2. Lies die 4 finalisierten Bullets aus dem Gesprächskontext dieser Session (ausgegeben von `/analyse-listing`).
3. Schreibe Intro-Zeile gefolgt von `\begin{itemize}...\end{itemize}` mit den 4 Bullets in `cv.tex`.

## 5. Resume Slots füllen

### 5.1. Makro-Ablauf

- Lies die Datei `application-pipeline/user-info/cv/content_pool.tex`. Hier sind Makros für Berufserfahrung, Ausbildung und Projekte hinterlegt.
- Lies die Datei `application-pipeline/user-info/triage-profile/candidate-profile.md`. Hier sind Identität plus Match-Kriterien hinterlegt.
- Behandle `content_pool.tex` als **Content Pool** mit deterministischen Resume-Projektionen.
- Arbeite pro Resume-Slot (`resume_berufserfahrung`, `resume_ausbildung`, `resume_projekte`) mit den dazugehörigen **Content Pool Candidates**. Diese Projektion ist die einzige Quelle für:
  - den Zielslot,
  - den Abschnitt / die Herkunft im Content Pool,
  - den Item-Namen,
  - den validierten Makro-Aufruf für den Slot-Body,
  - `always` als Pflichtstatus,
  - `group` für exklusive Alternativen,
  - validierte `relevance`-Metadaten,
  - die authored order innerhalb des Artefakts.

<Makro-Slot-Regeln>
- Beginne je Resume-Slot mit allen **Content Pool Candidates**, die für diesen Slot projiziert werden.
- Nimm Pflichtkandidaten (`always: true`) immer auf, vorbehaltlich exklusiver `group`-Alternativen.
- Kandidaten mit demselben `group`-Wert sind gegenseitig ausschließende Alternativen derselben Stelle. Wähle höchstens einen Kandidaten pro Gruppe.
- Nutze `relevance`-Metadaten und Candidate-Kontext als deterministische Hinweisstruktur des Artefakts.
- Die Auswahl optionaler Kandidaten bleibt urteilsbasiert: gleiche sie gegen das Listing sowie gegen `candidate-profile.md` ab.
- Ordne die gewählten Kandidaten innerhalb jedes Resume-Slots nach Relevanz und Candidate-Kontext; erhalte dabei die authored order als Tie-Breaker bzw. dort, wo das Artefakt keine stärkere Priorisierung vorgibt.
- Schreibe nur validierte Makro-Aufrufe aus den gewählten Candidates in den Slot-Body, genau ein Makro-Aufruf pro Zeile.
</Makro-Slot-Regeln>

### 5.2. Skills

Der `skills_block`-Slot wird mechanisch aus dem Skill-Pool zusammengesetzt.

1. Pool einlesen: Lies `application-pipeline/user-info/triage-profile/skills.md`.
2. Gruppenauswahl: `always`-Gruppen immer aufnehmen, weitere Gruppen nach Relevanz und fachlicher Einschätzung wählen.
3. Eintragsauswahl pro gewählter Gruppe: `always`-Einträge immer aufnehmen, weitere nach fachlicher Einschätzung wählen.
4. Gültigkeit der Auswahl prüfen: Jeder gewählte Eintragsname muss exakt im eingelesenen Pool vorkommen.
5. Schreiben: Für jede nicht-leere gewählte Gruppe genau eine Zeile in `%% SLOT: skills_block`:

<skill-gruppen-zeile>
\cvitem{<heading-text>}{<skill1>, <skill2>, ...}
</skill-gruppen-zeile>

## 6. Kompilieren

Rufe das Skript `application-pipeline compile-cv <application-folder>` auf. Erfolg bedeutet: `cover_<application-folder>.pdf`, `resume_<application-folder>.pdf` und `combined_<application-folder>.pdf` im Anwendungsordner.

Bei Exit-Code ungleich 0: teile dem Nutzer mit, dass die Kompilierung fehlgeschlagen ist, und hänge den `stderr` unverändert an. Dann stopp.

## 7. Seiten-Überlauf-Schleife

Lies die Seitenzahlen von `cover_<application-folder>.pdf` und `resume_<application-folder>.pdf`. `cover_<application-folder>.pdf` darf höchstens eine Seite haben, `resume_<application-folder>.pdf` höchstens zwei Seiten.

### 7.1. Interaktive Cover-Kürzung

Wenn `cover_<application-folder>.pdf` zu lang ist:

<cover-shortening-loop>
1. Erzeuge für jeden Absatz eine verkürzte Variante.
2. Zeige dem Nutzer die verkürzten Varianten vollständig in Prosa.
3. Lass den Nutzer eine oder mehrere Varianten auswählen.
4. Schreibe nur die vom Nutzer gewählten verkürzten Varianten nach `<application-folder>/cv.tex`. Lass alle anderen Slots unverändert.
5. Kompiliere erneut und prüfe die Seitenzahl erneut.
6. Wiederhole Schritt 1, falls weiterhin ein Cover-Überlauf besteht.
</cover-shortening-loop>

Dieser Loop schreibt ausschließlich nach `<application-folder>/cv.tex`.

### 7.2. Automatische Resume-Kürzung

Wenn `resume_<application-folder>.pdf` zu lang ist:

<resume-shortening-loop>
1. Erzeuge für jeden Resume-Slot eine verkürzte Variante.
2. Wähle die beste Kürzung aus.
3. Schreibe nur diese verkürzte Variante nach `<application-folder>/cv.tex`. Lass alle anderen Slots unverändert.
4. Kompiliere erneut und prüfe die Seitenzahl erneut.
5. Wiederhole Schritt 1, falls weiterhin ein Resume-Überlauf besteht.
</resume-shortening-loop>

## 8. Erfolgsrückmeldung

Sobald beide PDFs ihre Seitenlimits einhalten: gib eine kurze Zusammenfassung in Prosa aus mit dem Pfad zum Application-Ordner, den drei erzeugten PDFs inkl. Finalseitenzahlen und dem Anschreibentext.

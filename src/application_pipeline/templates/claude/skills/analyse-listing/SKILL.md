---
name: analyse-listing
description: Grills the user about why they want to apply to a specific listing and writes the conclusion into a per-listing application folder. Always one listing per session. Runs when the user types /analyse-listing.
---

# /analyse-listing

Interviewe den User unermüdlich zu jedem Aspekt, warum er sich auf diese Position bewerben will, bis ihr ein gemeinsames Verständnis erreicht habt. Gehe jeden Branch des Entscheidungsbaums durch und löse Abhängigkeiten zwischen Entscheidungen einzeln auf. Gib pro Frage eine eigene Empfehlung mit.

Stelle die Fragen einzeln und warte auf Feedback pro Frage.

Wenn eine Frage durch Lesen des Triage-Profils oder der raw_description des Listings beantwortbar ist, lies stattdessen.

**Eine Session = ein Listing.** Keine Batch-Iteration.

Universalregeln: [../_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md) und [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md).

## Startup-Checks

Führe die Checks aus [../_shared/STARTUP-TRIAGE.md](../_shared/STARTUP-TRIAGE.md) aus.

## Argument-Disposition

Aus dem Argument den Eingabe-Modus ableiten:

- **leer** User um Listing-Paste oder Datum bitten, dies ist dann das Argument
- **`today` / `last`** → das aktuellste File aus `application-pipeline/results/` laden (lexikographisch größter Name; die Files sind ISO-datiert).
- **Datum** auf `YYYY-MM-DD` normalisieren und `application-pipeline/results/<YYYY-MM-DD>.md` laden.
- **alles andere** → das Argument selbst ist das Listing.

Fehlt die angeforderte Results-Datei: dem User klar sagen, dann stopp.

## Listing wählen

**Direkt-Paste-Modi** (leer / arg-text): genau ein Listing, direkt verwendet.

**Results-File-Modi** (`today` / `last` / ISO-Datum): die Datei in einer LLM-Runde in strukturierte Karten `{company, title, posted_date, summary, raw_description, url, rank}` parsen. Im Default-Layout sind Karten durch `---` und eine URL-Zeile getrennt; der User darf das Layout angepasst haben — vertraue dem eigenen Splitting.

- Enthält die Datei **genau eine** Karte: direkt verwenden.
- Enthält sie **mehrere** Karten: kurze nummerierte Übersicht (Rank, Company, Title, Ein-Zeilen-Summary) zeigen und den User fragen, welches **einzelne** Listing er analysieren will. Eine Wahl, dann weiter.

## Listing bestätigen

Extrahiere `Company` und `Role` aus dem gewählten Listing per eigenem Lesen. Das Ordner-Datum ist immer **heute**, unabhängig vom `posted_date` im Body.

Baue den Ordner-Slug nach den Slug-Regeln unten. Bestätige Company, Role und resultierenden Pfad (`application-pipeline/applications/<today>-<Company-slug>_<Role-slug>/`) mit dem User. Erlaube Overrides für Company und Role; bei Änderungen neu sluggen und erneut bestätigen lassen.

Dann mit `{company, title, raw_description, url}` (url=None für Paste-Modi) in die Grilling-Schleife.

## Per-Turn-Flow

<per-turn-flow>
Pro Turn in der Grilling-Schleife, in dieser Reihenfolge:

1. **Lies die User-Antwort.**
2. **Vergleiche die Antwort mit den bestehenden Triage-Profil-Bullets** und extrahiere verallgemeinerbare Signale für `candidate-profile.md` — auch wenn sie bestehende Bullets vertiefen, differenzieren oder korrigieren, nicht nur wenn sie net-new sind.
3. **Schreibe Profil-Updates** laut [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md) (Routing, Conservative-Promotion, Supersede-Replace, Bullet-Stil).
4. **Formuliere die nächste Frage** (oder gehe zur Session-Ende-Prüfung über).

Schritte 1–3 müssen abgeschlossen sein, bevor Schritt 4 emittiert wird.
</per-turn-flow>

## Während des Grillings

### Triage-Profil challengen

Wenn der User etwas sagt, das einem existierenden Bullet in einer der drei Profil-Files widerspricht: sofort aufdecken. *„In `gate-criteria.md` steht X — du beschreibst hier eher Y. Was stimmt?"*

### Cross-Reference mit der raw_description

Wenn der User behauptet, was die Position fordert: prüfe, ob die `raw_description` zustimmt. Widersprüche sofort aufdecken.

### Resonance-Branches aktiv erfragen

Tailoring-Hooks haben zwei Sorten — beide müssen im Grilling vorkommen:

- **Resonance** — Listing-*Signale* (nicht Anforderungen), die den User anziehen. Zeigen *warum die Position zu ihm passt* — nicht *warum er sie erfüllt*.
- **Competence** — Listing-*Anforderungen* und welcher User-Background sie deckt.

Wenn das Grilling bisher nur Competence-Branches abgedeckt hat, frage aktiv nach Resonance: welche Listing-Signale ziehen den User an, und warum — Identität, Werte, biographisches Muster.

### Hooks offensiv, nicht defensiv

- Keine Listing-Anforderung aufgreifen, die der User nicht erfüllt, nur um sie zu entkräften. Insbesondere `idealerweise`-Anforderungen weglassen — sie sind keine Pflicht und müssen nicht verteidigt werden.
- Anti-Frames in Worten aktiv vermeiden — sie machen den Frame erst sichtbar. Positive Substanz reicht; der Leser zieht die Schlüsse selbst.
- Competence-Hooks als positive Behauptung formulieren, nicht als Konter zu einer fehlenden Anforderung.

## Session-Ende

Wenn du denkst, die großen Branches sind durch (du hast (a) ein konkretes *Warum gerade diese Position*, (b) **mindestens je einen Resonance- und einen Competence-Hook**, insgesamt 2–4 Tailoring-Hooks, (c) keine ungelösten Zweifel): gib den Ball zurück mit einem Draft des `analysis.md`-Inhalts inline zur Prüfung und der Frage, ob das passt oder ob noch etwas unter Druck zu setzen ist.

- **User-OK** → schreibe `application-pipeline/applications/<today>-<Company-slug>_<Role-slug>/analysis.md` nach dem Schema unten. Überschreibe ohne Rückfrage, falls bereits vorhanden. Dann eine kurze Prosa-Zusammenfassung mit dem geschriebenen Pfad, Notiz zu Profil-Änderungen, und vorgeschlagenem `/write-cv`-Aufruf mit **vollqualifiziertem** Pfad (`application-pipeline/applications/<today>-<slug>/`), damit der User unabhängig von der CWD copy-pasten kann.
- **Weiteres Feedback** → zurück ins Grilling, später erneut präsentieren.
- **Manueller Session-Abbruch** → nichts schreiben, kein Ordner. (Triage-Profil-Updates dieser Runde bleiben — sie sind bereits inline geschrieben.)

## Form von `analysis.md`

Wird nur bei User-OK geschrieben. Pfad: `application-pipeline/applications/<today>-<Company-slug>_<Role-slug>/analysis.md`.

<analysis-template>
# {Company} · {Title}

{raw_description verbatim}

## Why apply

- <2–4 Bullets: persönliche Verbindung zu Company/Rolle — Material für den Anschreiben-Opener>

## Tailoring hooks

### Resonance — warum das Listing zu mir passt

- **Listing-Signal:** <was im Listing den User anzieht — Rolle, Kultur, Greenfield, Domain, Kollegen>
  **Resonanz:** <warum es ihn anzieht — Identität, Werte, biographisches Muster>
  **Anekdote:** <konkrete Story, die das Muster im User-Leben belegt — max. 2–4 Zeilen, Anschreiben-tauglich>
- ...

### Competence — was ich mitbringe

- **Listing fordert:** <konkrete Anforderung aus der raw_description>
  **Hook:** <Erfahrung/Skill, als positive Substanz formuliert — nicht als Konter>
  **Anekdote:** <konkrete Story / Metrik — max. 2–4 Zeilen, Anschreiben-tauglich>
- ...
</analysis-template>

Referenziere `content_pool.tex` nicht — `/write-cv` matched Hooks selbst gegen Content-Pool-Items.

## Slug-Regeln (auf Company und Role gleich angewendet)

1. Strippe Gender-Marker: `(m/w/d)`, `(m/f/d)`, `(d/w/m)`, `(w/m/d)` und Äquivalente.
2. Strippe Trailing-Location-Segmente: alles nach dem letzten `—` / `–` / ` - `, falls der Schwanz nach einem Ort aussieht.
3. Transliteriere deutsche Umlaute: `ä→ae`, `ö→oe`, `ü→ue`, `ß→ss` (und großgeschriebene Varianten).
4. Ersetze jeden Lauf von Non-`[A-Za-z0-9]` durch ein einzelnes `-`.
5. Strippe führende/abschließende `-`.
6. Trunkiere bei 40 Zeichen (am letzten `-` vor dem Limit schneiden).
7. Großschreibung beibehalten.

## Schreib-Whitelist

<hard-rules>
Dieser Skill schreibt ausschließlich in:

- `application-pipeline/applications/<today>-<slug>/analysis.md`
- `application-pipeline/user-info/triage-profile/gate-criteria.md`
- `application-pipeline/user-info/triage-profile/candidate-profile.md`

Alles andere im Repo ist read-only — insbesondere `writing-style.md`.
</hard-rules>

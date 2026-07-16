---
name: analyse-listing
description: Extrahiert Anforderungen aus einer Stellenausschreibung, gleicht sie mit der Bullet Library ab und finalisiert mit dem Nutzer genau 4 Bullets für das Anschreiben.
disable-model-invocation: true
---

# Universalregeln

[_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md)

<write-rules>
Dieser Skill schreibt ausschließlich in:

- `application-pipeline/user-info/cv/bullet-library.md`
- `application-pipeline/user-info/triage-profile/candidate-profile.md`
</write-rules>

# Aufgabe

## 1. Listing laden

Leite den Eingabe-Modus aus dem Argument ab:

- **leer** → bitte den Nutzer, ein Listing einzufügen oder ein Datum anzugeben
- **`today` / `last`** → lade die lexikographisch größte Datei aus `application-pipeline/results/`
- **Datum** → normalisiere auf `YYYY-MM-DD` und lade `application-pipeline/results/<YYYY-MM-DD>.md`
- **alles andere** → das Argument selbst ist das Listing

Fehlt eine angeforderte Datei: dem Nutzer klar sagen, dann stopp.

Enthält eine Results-Datei mehrere Listings: zeige eine kurze nummerierte Übersicht und frage, welches einzelne Listing analysiert werden soll.

## 2. Anforderungen extrahieren und Bullets abgleichen

Lies das Listing und extrahiere alle Anforderungen an den Kandidaten. Lies danach `application-pipeline/user-info/cv/bullet-library.md`.

Zeige dem Nutzer eine strukturierte Übersicht auf einmal:

- Pro Anforderung: welche Bullets passen (direkt oder indirekt)
- Anforderungen ohne passenden Bullet: am Ende als Lücken markiert

## 3. Vier Bullets finalisieren

Schlage sofort die 4 am besten passenden Bullets als Startpunkt vor. Iteriere dann mit dem Nutzer:

- Lücken → neuen Bullet formulieren und mit dem Nutzer iterieren bis zur Zufriedenheit
- Der Nutzer kann jederzeit Bullets streichen, tauschen oder neue beauftragen

Neue Bullets sofort wortgenau in `bullet-library.md` aufnehmen. Verallgemeinerte Fakten über den Kandidaten, die über dieses Listing hinausgehen, still in `candidate-profile.md` schreiben.

## 4. Finalisierte Bullets ausgeben

Sobald genau 4 Bullets feststehen: gib alle 4 mit exakt dem Wortlaut aus der Bullet Library aus. Schreibe darunter einen vorgeschlagenen `/write-cv`-Aufruf.

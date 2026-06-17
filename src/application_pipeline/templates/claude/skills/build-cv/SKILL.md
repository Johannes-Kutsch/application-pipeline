---
name: build-cv
description: Erstellt aus einem gefuellten /write-cv Ordner die Cover-, Resume- und Combined-PDFs. Nach jedem Build werden Cover oder Resume in Folge-Loops gekuerzt, bis beide PDFs die Laengenlimits einhalten.
---

# /build-cv

# Aufgabe

Erstelle aus der `cv.tex`-Datei im `<application-folder>` drei PDFs (Cover, Resume, Combined).

## 1. Kompilierungsaufruf

Rufe das Skript `application-pipeline compile-cv <application-folder>` auf. Erfolg bedeutet: `cover_<application-folder>.pdf`, `resume_<application-folder>.pdf` und `combined_<application-folder>.pdf` im Anwendungsordner.
Bei Exit-Code ungleich 0 teile dem Nutzer mit, dass die Kompilierung fehlgeschlagen ist, und hänge den `stderr` unverändert an. Danach stoppen.

## 2. Seiten-Überlauf-Schleife

Nach erfolgreicher Kompilierung: Lese die Seitenzahlen von `cover_<application-folder>.pdf` und `resume_<application-folder>.pdf`. `cover_<application-folder>.pdf` darf höchstens eine Seite haben, `resume_<application-folder>.pdf` höchstens zwei Seiten.

- Resume-Überlauf läuft automatisch nach dem untenstehenden Kürzungsdurchlauf.
- Cover-Überlauf läuft interaktiv nach dem Abschnitt *Cover-Kürzung* statt vollständig automatisch.

### 2.1. Interaktive Cover-Kürzung

Wenn `cover_<application-folder>.pdf` zu lang ist:

<cover-shortening-loop>
1. Erzeuge für jeden Absatz eine verkürzte Variante.
2. Zeige dem Nutzer die verkürzten Varianten vollständig in Prosa.
3. Lass den Nutzer genau eine oder mehrere Varianten auswählen.
4. Schreibe nur die vom Nutzer gewählten verkürzten Varianten nach `<application-folder>/cv.tex`. Lass alle anderen Slots unverändert.
5. Erstelle erneut die PDF-Dateien und prüfe die Seitenzahl erneut.
6. Wiederhole Schritt 1, falls weiterhin ein Cover-Überlauf besteht.
</cover-shortening-loop>

Wichtig: Dieser Nachkompilierungsdurchlauf schreibt niemals nach `cover-patterns.md`.

### 2.2. Automatische Resume-Kürzung

Wenn `resume_<application-folder>.pdf` zu lang ist:

<resume-shortening-loop>
1. Erzeuge für jeden Resume-Slot eine verkürzte Variante.
2. Wähle die beste Kürzung aus.
3. Schreibe nur diese verkürzte Variante nach `<application-folder>/cv.tex`. Lasse alle anderen Slots unverändert.
4. Erstelle erneut die PDF-Dateien und prüfe die Seitenzahl erneut.
5. Wiederhole Schritt 1, falls weiterhin ein Resume-Überlauf besteht.
</resume-shortening-loop>

## 3. Erfolgsrückmeldung

Wenn die Schleife erfolgreich konvergiert: Gib eine kurze Zusammenfassung in Prosa aus mit dem Pfad zum Application-Ordner, den drei erzeugten PDFs inkl. Finalseitenzahlen und dem Anschreibentext.

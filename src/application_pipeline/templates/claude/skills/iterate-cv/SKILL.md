---
name: iterate-cv
description: Applies conversational feedback to an existing cv.tex CV Slot-Map and/or to analysis.md, edits per-slot bodies in place, recompiles via the LaTeX build script when cv.tex was touched, and promotes generalisable signals into the triage profile. Resident loop — ends when the user signals done. Runs when the user types /iterate-cv.
---

# /iterate-cv

Schließt den Edit-Loop nach `/write-cv` **oder** nach `/analyse-listing`, wenn der User einen Fehler in der `analysis.md` korrigieren will. Der User gibt **konversationelles Feedback** dazu, was geändert werden soll — er ediert weder die `.tex` noch die `analysis.md` von Hand. Jedes Anliegen wird einem von vier Buckets zugeordnet und auf die richtige(n) Datei(en) angewandt; berührt der Turn `cv.tex`, wird neu kompiliert.

`cv.tex` ist eine **CV Slot-Map** — siehe [../_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md) für Format-Spec und Slot-Listen-Source-of-Truth.

Universalregeln: [../_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md) und [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md).

## Argumente

Siehe [../_shared/APPLICATION-FOLDER-ARG.md](../_shared/APPLICATION-FOLDER-ARG.md).

## Startup-Checks

Führe die Checks aus [../_shared/STARTUP-APPLICATION.md](../_shared/STARTUP-APPLICATION.md) aus.

## Resident-Loop

Nach den Startup-Checks: lies `analysis.md`, alle `application-pipeline/user-info/triage-profile/*.md`-Files und `cv.tex` als Slot-Map laut [../_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md) in den Speicher. `application-pipeline/cv-template/cv_skeleton.tex` wird **nicht** routinemäßig gelesen Frage den User, was geändert werden soll.

Die Slot-Bodies werden **nicht** unaufgefordert dem User zurückgespiegelt — er kennt seinen Draft. Berührt ein Anliegen einen Slot, wird genau dieser Slot (und nur dieser) im Turn-Output sichtbar.

Dann schleifen:

1. **Snapshotte** `cv.tex` (falls vorhanden), `analysis.md` und die drei `user-info/triage-profile/*.md`-Files im Speicher, bevor irgendeine Änderung dieses Turns angewandt wird — auch in Analysis-only-Turns, weil ein einzelner Turn beide Buckets mischen kann. Das ist die Revert-Basis für „Compile-Fehler mitten in der Iteration".
2. **Lies das Anliegen** des Users (eine Nachricht kann mehrere distinkte Feedback-Teile enthalten; vertraue der eigenen Dekomposition).
3. **Erkenne Exit per Prosa-Intention.** Wenn der User in irgendeiner Form signalisiert, dass er fertig ist („passt", „so lassen", „fertig", leerer Input): geh in den *Exit*-Schritt. Bei Mehrdeutigkeit kurz nachfragen, ob als final zu verstehen, statt einen Token zu erzwingen.
4. **Pro Anliegen:** Per-Concern-Flow unten.
5. **Falls `cv.tex` in diesem Turn berührt wurde:** einmal kompilieren am Ende des Turns laut [../_shared/BUILD-CONTRACT.md](../_shared/BUILD-CONTRACT.md). Bei Erfolg den Seiten-Overflow-Strip-Down-Loop laut [../_shared/STRIP-DOWN.md](../_shared/STRIP-DOWN.md) laufen lassen. Wurde `cv.tex` nicht berührt: kein Build, kein Strip-down.
6. Re-prompt nach dem nächsten Anliegen.

## Per-Concern-Flow

1. **Klassifiziere** in einen von vier Buckets — die Quelle des Anliegens entscheidet:
   - **Style-Signal** — Stimm-/Phrasing- **oder Cover-Strategie-**Muster, das auch für künftige Drafts gelten soll. Sub-Routing entscheidet die Ziel-Datei und Sektion:
     - *Regel-Form, Phrasing* ("vermeide X", "X klingt nach LLM", "mach immer Y"): One-Liner-Edit in `cv/writing-style.md` (Don't- oder Do-Zeile, knapp, deklarativ, kein Beleg-Exemplar dahinter).
     - *Strategie-Form, Inhalt/Bogen/Beleg pro Slot* ("Bootcamp nicht im Brief", "Sinnstiftungs-Pivot passt hier nicht", "nur ein Anker pro Slot", "Meta-Beleg nicht doppelt"): Bullet in `cv/writing-style.md` Sektion `## Cover-Strategie`, knapp, deklarativ.
     - *Vorbild-Form* ("der Ton von Brief X war besser", "schreib mehr wie Vorbild N", "dieser Snippet trägt das Muster Y"): Edit in `cv/positive-exemplars.md` — entweder Tonangabe-Annotation eines bestehenden Vorbilds präzisieren, oder neues Vorbild/Snippet aufnehmen (Quelle: realer handgeschriebener Brief im Repo, nicht Agent-Output).
     - **Hard ban:** keine Negativ-Exemplare in `cv/writing-style.md` oder `cv/positive-exemplars.md` anlegen. Wenn ein konkreter Failure-Satz aus dem Draft Auslöser ist, abstrahiere zur Regel (Don't- oder Strategie-Bullet) und verwirf den Beispiel-Satz.
   - **Fakt-über-mich** — Berufsweg, Skill, Präferenz, Identitäts-Signal, das über dieses Listing hinaus verallgemeinert. Ziel: eines von `candidate-profile.md` / `gate-criteria.md` (Routing siehe [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md)).
   - **Position-spezifisch** — nur für dieses Listing relevant, betrifft den CV-Draft. Ziel: nur `cv.tex`.
   - **Analysis-Fakt-Korrektur** — der User adressiert eine Stelle, die ausschließlich in `analysis.md` lebt (Tailoring-Hook, Why-Apply-Bullet, Fit-Aussage) und die faktisch falsch ist. Ziel: `analysis.md` **plus** Root-Cause in `user-info/triage-profile/*.md`. Siehe eigener Sub-Flow unten.

2. **Wenn die Klassifizierung mehrdeutig ist** oder eine Profil-Datei auf schwachem Signal umgeschrieben würde: kurze Grilling-Sub-Session im Geist von `/grill-me` — eine Frage pro Turn, Deutsch, jeweils mit empfohlener Antwort, bis Änderung und Zielort klar sind. Für eindeutig Position-spezifisches Feedback überspringen — Grilling ist Eskalation, nicht Default.

3. **Wende die Änderung an** — inline im selben Turn, nicht batchen.
   - **Position-spezifisch** → identifiziere den/die betroffenen Slot(s) in der Slot-Map (siehe „Slot-Identifikation" unten) und editiere ausschließlich diese(n) Body.
   - **Style-Signal** → `cv/writing-style.md` (Phrasing-Regel-Form ins Do/Don't, Strategie-Form in `## Cover-Strategie`) **oder** `cv/positive-exemplars.md` (Vorbild-Form), laut Sub-Routing oben und [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md). Die resultierende Regel bzw. das neue Vorbild zusätzlich auf die Slot-Bodies in `cv.tex` anwenden, wo es den aktuellen Draft verändern würde (falls `cv.tex` existiert) — Slot-Granularität wie unter Position-spezifisch.
   - **Fakt-über-mich** → die geroutete `user-info/triage-profile/*.md`-Datei laut [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md) schreiben. Sichtbare Konsequenzen in die betroffenen Slot-Bodies von `cv.tex` mit-einarbeiten (falls `cv.tex` existiert).
   - **Analysis-Fakt-Korrektur** → Sub-Flow unten.

4. **Slot-Identifikation (für `cv.tex`-Edits).** Aus dem Wortlaut des Feedbacks und dem aktuellen Body-Inhalt ableiten, welche(r) Slot(s) betroffen sind. Mehrere Slots in einem Anliegen sind erlaubt (z.B. „die Anrede passt nicht zum Empfänger" → `recipient_name` + `opening`). Ist nicht klar, welcher Slot gemeint ist oder wie das Feedback im Kontext des Slot-Zwecks zu interpretieren ist: lies **gezielt** den passenden Slot-Block aus `application-pipeline/cv-template/cv_skeleton.tex` (Header + die `% …`-Guidance-Kommentare unmittelbar danach) und nutze die Guidance, um Intent und Zielort aufzulösen. Nur lesen, nicht im Voraus laden, und nicht den ganzen Skeleton-Inhalt — nur den/die relevanten Block(s).

5. **`cv.tex` zurückschreiben.** Nach allen Slot-Edits dieses Turns: die gesamte Slot-Map mit unveränderten Headern und in unveränderter Reihenfolge serialisieren und nach `<app_dir>/cv.tex` schreiben. Alle kanonischen Slots müssen präsent bleiben — auch unveränderte. Nach dem Schreiben muss `cv.tex` mit dem Slot-Map-Parser sauber parsen.

6. **Content-Pool-Item-Feedback.** Wenn das Feedback ein konkretes Erfahrungs-Item ist, das für künftige CVs verfügbar sein soll: Änderung für *dieses* Listing nur auf `cv.tex` anwenden und den User einmal in Prosa darauf hinweisen, dass dauerhafte Pool-Pflege manuell in `application-pipeline/user-info/cv/content_pool.tex` passieren muss.

## Sub-Flow: Analysis-Fakt-Korrektur

Symptom in `analysis.md` und Root-Cause in `user-info/triage-profile/*.md` werden **gemeinsam** behandelt — nie nur das eine.

1. **Symptom lokalisieren.** Finde die konkrete Stelle in `analysis.md` (Hook, Why-Apply-Bullet, Fit-Satz), die der User adressiert.
2. **Root-Cause identifizieren.** Welche Aussage in welcher `user-info/triage-profile/*.md` hat zu dieser Stelle geführt? Meistens offensichtlich aus dem Inhalt (z.B. „pycastle-Konsument" als Hook → entstand aus der `application-pipeline`-Zeile in `candidate-profile.md`). Routing der Root-Cause-Datei laut [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md).
3. **Wenn die Root-Cause nicht eindeutig zuordenbar ist:** Grilling-Sub-Session, bis Quelle und Fix klar sind.
4. **Edge-Case: keine Root-Cause in `user-info/triage-profile/`.** Wenn das Grilling ergibt, dass keine Aussage in `user-info/triage-profile/` den Fehler erklärt (z.B. `/analyse-listing` hat einen Listing-Inhalt halluziniert, der so gar nicht im Stellentext stand): nur `analysis.md` korrigieren und in Prosa explizit sagen „keine Root-Cause in `user-info/triage-profile/` identifiziert — vermutlich Halluzination beim Analyse-Lauf". Kein erzwungenes `user-info/triage-profile/`-Edit.
5. **Beide Edits anwenden** — `analysis.md` und (sofern vorhanden) die geroutete `user-info/triage-profile/*.md`-Datei laut [../_shared/TRIAGE-ROUTING.md](../_shared/TRIAGE-ROUTING.md) schreiben.
6. **Falls `cv.tex` existiert:** sichtbare Konsequenzen aus der Root-Cause-Änderung dort mit-anwenden.

## Compile-Fehler mitten in der Iteration

Greift nur, wenn dieser Turn `cv.tex` berührt hat und der Build dann Non-Zero-Exit liefert. Stoppe den Loop **nicht**. Stattdessen:

1. **Revertiere** `cv.tex`, `analysis.md` und jede `user-info/triage-profile/*.md`-Datei, die in diesem Turn geschrieben wurde, auf den Pre-Turn-Snapshot aus Resident-Loop Step 1. Der Draft auf Disk bleibt kompilierbar.
2. Sag dem User in Prosa, dass der Compile fehlgeschlagen ist und die Änderungen dieses Turns zurückgenommen wurden, mit dem stderr verbatim als Anhang.
3. Re-prompt für die nächste Anweisung.

## Exit

Wenn der User in Prosa signalisiert, dass er fertig ist: gib in eigenen Worten eine kurze Zusammenfassung.

- Existiert `cv.tex` und wurde in dieser Session kompiliert: Pfad zum Application-Ordner plus die drei PDF-Dateinamen mit aktuellen Seitenzahlen.
- Existiert `cv.tex` nicht oder wurde in dieser Session nicht berührt: Pfad zum Application-Ordner plus Liste der geänderten Files (`analysis.md` und/oder `user-info/triage-profile/*.md`).

## Schreib-Whitelist

<hard-rules>
Dieser Skill schreibt ausschließlich in:

- `<application-folder>/cv.tex` (ausschließlich Slot-Bodies; Shape-Constraints siehe [../_shared/SLOT-MAP.md](../_shared/SLOT-MAP.md))
- `<application-folder>/analysis.md` (ausschließlich im Per-Concern-Bucket *Analysis-Fakt-Korrektur*)
- `application-pipeline/user-info/triage-profile/*.md`
- `application-pipeline/user-info/cv/writing-style.md`
- `application-pipeline/user-info/cv/positive-exemplars.md`

Alles andere im Repo ist read-only — insbesondere `content_pool.tex`, `facts.tex`, `cv_skeleton.tex`. Wenn ein User-Anliegen ein Content-Pool-Item wäre, gilt der Hinweis aus Per-Concern-Flow Schritt 6.
</hard-rules>

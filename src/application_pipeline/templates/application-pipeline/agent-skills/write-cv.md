## Startup-Checks

Falls `<application-folder>/cv.tex` bereits exitiert, frage den User ob er die Datei neu generieren, nur die pdf's builden oder direkt den bestehenden Draft iterieren möchte.

Prüfe zusätzlich:

- `application-pipeline/user-info/cv/` enthält neben den Triage-Files auch `facts.tex`, `content_pool.tex`, `signature.png` und `profile.png`
- `application-pipeline/cv-template/cv_skeleton.tex` existiert relativ zur CWD
- `analysis.md` existiert im aufgelösten Application-Ordner

Fehlt `analysis.md`: dem User sagen, dass `/analyse-listing` zuerst laufen muss, dann stopp.

Fehlt eine Datei in `user-info/cv/` oder das Skelett: dem User in eigenen Worten sagen, welche Datei fehlt und dass `application-pipeline init --refresh` sie initialisiert. Dann stopp.

# Universalregeln

[_shared/CONVENTIONS.md](_shared/CONVENTIONS.md).

## Argumente

| Argument | Bedeutung |
|---|---|
| *(leer)* | Das aktuelle Arbeitsverzeichnis ist der Application-Ordner. |
| `<path>` | Application-Ordnerpfad. Akzeptiere kurz (`applications/<slug>/`), qualifiziert (`application-pipeline/applications/<slug>/`) und nackt (`<slug>/`); intern immer auf `application-pipeline/applications/<slug>/` auflösen. |

## Inputs einlesen

Lies alle Inputs in den Speicher:

- `analysis.md` — neutraler Listing-Summary + Hook/Why-Blocks pro Absatz + `Cover strategy` (ein Lead-Hook mit Supporting/Reserve-Hooks) + `Cover sections` (`intro`, `bridge`, `evidence`, `closing`) als direkter Handoff fuer die vier Cover-Prosa-Slots + Listing-Forderungen / Hook / Anekdote-Tailoring-Hooks.
- `application-pipeline/user-info/triage-profile/*.md` — Identität + Match-Kriterien. Geladen: `candidate-profile.md`.
- `application-pipeline/user-info/cv/cover-patterns.md` — die **einzige** Cover-Prosa-Quelle. Missing oder leer ist ein Bootstrap-Zustand, kein Startup-Fehler. Parse strikt: pro Pattern genau ein `##`-Name, `slot`, `argument_type`, `use_when`, `placeholders`, `why_it_works` und genau ein Absatz Text. Platzhalter sind die erlaubte Muster-Vokabel; `Musterprojekt` steht fuer Candidate-Evidence, `Musterprodukt` fuer das Produkt oder die Plattform des Arbeitgebers.
- `application-pipeline/user-info/cv/content_pool.tex` — jeder `%%% ITEM: …`-Block mit den drei Feldern `always`, `group` (optional), `relevance`. Section wird aus der nächstgelegenen vorausgehenden `% ===== <name> =====`-Blocküberschrift abgeleitet. **Die `\newcommand`-Bodies werden roh als TeX in den Prompt aufgenommen** — kein Stripper, keine Escape-Regeln.
- `application-pipeline/user-info/triage-profile/skills.md` — **Skills-Pool** laut ADR-0033. H2-Headings sind Skill-Gruppen (Heading-Text = `\cvitem{<group>}{...}`-Kategorie-Label), `-`-Bullets sind Skill-Namen. Optionaler pandoc-style `{...}`-Attributblock am Zeilenende: Gruppen tragen `always` (bare) und `<jobtype>=<high|medium|low>`-Relevanz-Einträge; Items tragen nur `always`. Bullets vor der ersten H2 werden ignoriert (`/write-cv` rendert nur gruppierte Skills). Unbekannte Attribute werden ignoriert. Source-of-Truth — keine Skill-Namen aus anderen Quellen.
- `application-pipeline/cv-template/cv_skeleton.tex` — das **CV-Skelett**. Diese Datei ist der Format-by-Example und die Source-of-Truth für die Slot-Liste. Jeder `%% SLOT: <name>`-Block enthält Prompt-Guidance-Kommentare (`% …`-Zeilen direkt nach dem Slot-Header) plus einen Beispiel-Body. Beide werden ausgewertet.

## Profil-Routing

[_shared/TRIAGE-ROUTING.md](_shared/TRIAGE-ROUTING.md)

## CV Slot-Map entwerfen

Format-Spec, Header-Form, Body-Semantik und Slot-Listen-Source-of-Truth: siehe [_shared/SLOT-MAP.md](_shared/SLOT-MAP.md).

**Pro Slot:**

- Die Prompt-Guidance-Kommentare unmittelbar nach dem `%% SLOT:`-Header (alle `% …`-Zeilen bis zum ersten Nicht-Kommentar-Inhalt) sind verbindliche Anweisungen für diesen Slot. Halte dich daran (Tonfall, Längenhinweise, was rein darf / was nicht).
- Der Beispiel-Body im Skelett ist Format-by-Example: Stil, TeX-Konstrukte (`\href`, `\textit`, `\cventry`, …) und Mehrzeiligkeit zeigen, wie der echte Body aussehen soll. Nicht den Beispiel-Body wörtlich kopieren.
- Die Prompt-Guidance-Kommentare aus dem Skelett werden **nicht** in `cv.tex` übernommen — die emittierte Datei enthält nur Slot-Header und Body.

**`opening`:** automatisch aus `analysis.md` und Skeleton-Guidance schreiben. Der Opener beginnt mit einem persönlicher, listingspezifischer Resonanz-Hook; keine Mehrfach-Nennung von Projektnamen im Opener. Kein User-Loop fuer `opening`.

**Cover-Paragraph-Slots (`cover_intro`, `cover_pivot`, `cover_fit`, `cover_closing`):** Nutze den `Lead hook` aus der `Cover strategy` in `analysis.md` als dominanten roten Faden; `Supporting hooks` dürfen ihn stützen, `Reserve hooks` bleiben für Resume, Skills oder spätere Iteration liegen. Die `Cover sections` sind der direkte Handoff ohne ad hoc Remapping: `cover_intro` aus `intro`, `cover_pivot` aus `bridge`, `cover_fit` aus `evidence`, `cover_closing` aus `closing`. In den mittleren Cover-Slots entsteht ein dominanter Capability-Arc mit höchstens zwei Evidence-Anchors, die denselben Argumentstrang verlängern. Octofox, pycastle und application-pipeline sind selektierbare Evidence-Anchors, nicht feste Absatz-Slots. Weitere Projekte bleiben für Resume-Slots, Skills-Block oder spätere Iteration. Geerdet in den Hook/Why-Blocks aus `analysis.md`, den `Cover sections` und den Tailoring-Hooks. Erfinde **keine** Fakten; jede Behauptung lässt sich auf `analysis.md` oder `candidate-profile.md` zurückführen.

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
2. **Jobtype aus dem Listing ableiten.** Bestimme den Jobtype des aktuellen Listings (z.B. `mle`, `games`, `agents`) per LLM-Judgment aus `analysis.md` (neutraler Listing-Summary + Hook/Why-Blocks + Tailoring-Hooks). Der Jobtype-Schlüssel entspricht den Relevanz-Keys im Pool — wähle deine Bezeichnung passend zu den vorhandenen Keys.
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

## Reihenfolge der Draft-Erstellung

Arbeite in dieser Reihenfolge:

1. Recipient-Slots automatisch füllen.
2. `opening` automatisch schreiben.
3. Resume-Slots automatisch aus dem Content-Pool assemblieren.
4. `skills_block` mechanisch assemblieren.
5. Erst danach die vier Cover-Prosa-Slots **interaktiv** einzeln draften: `cover_intro`, `cover_pivot`, `cover_fit`, `cover_closing`.

Die Cover-Schleife sieht den bereits zusammengestellten Resume- und Skills-Stand, damit sie Evidence nicht unnötig doppelt belegt.

## Interaktives Cover Drafting

Die vier Cover-Prosa-Slots werden **nacheinander** bearbeitet; immer genau ein Slot gleichzeitig. Vor dem ersten User-Prompt darfst du die automatische Vorarbeit aus der Reihenfolge oben komplett erledigen. `opening` bleibt ausserhalb dieser Cover-Absatz-Schleife.

Pro Slot:

1. Leite aus `analysis.md` den Slot-Zweck und den wahrscheinlich passendsten `argument_type` ab, gestützt auf Hook und Why des jeweiligen Absatzes.
2. Suche in `cover-patterns.md` nach **einem** klaren Match fuer genau diesen Slot und diesen Argument-Typ.
3. Wenn ein klarer Match existiert:
   - praesentier genau **einen** Vorschlag als **Cover Paragraph Pattern**-Match;
   - sag knapp, warum dieses Pattern passt;
   - frag den User, ob er den Absatz fuer diesen Slot akzeptiert.
4. Wenn kein klarer Match existiert **oder** der User den Match ablehnt:
   - praesentier genau **drei** Alternativen;
   - jede Alternative muss einen **anderen** `argument_type` haben;
   - jede Alternative wird als voll ausformulierter Absatz gezeigt, nicht als Outline.
5. Nach User-Entscheid gilt:
   - bestaetigter vorhandener Pattern-Match oder bestaetigte leichte Adaption schreibt **nur** in den Slot von `cv.tex`;
   - ein bestaetigter neuer Absatz darf in `cover-patterns.md` gespeichert werden, aber nur wenn er gegenueber der bestehenden Bibliothek **signifikant neu** ist, also eine neue Slot-Purpose-plus-Argument-Type-Kombination oder eine klar neue argumentative Bewegung bietet;
   - bei Grenzfaellen frag kurz nach, ob der User den Absatz nur fuer dieses Listing oder dauerhaft als neues Pattern sichern will;
   - auf expliziten User-Wunsch darf ein neuer signifikanter Absatz auch dann nach `cover-patterns.md` geschrieben werden, wenn du ihn nicht von dir aus als speicherwuerdig markiert haettest.
6. Jeder bestaetigte Cover-Absatz wird sofort nach `<application-folder>/cv.tex` geschrieben. Serialisiere dabei immer die komplette Slot-Map mit allen kanonischen Slots in unveraenderter Reihenfolge; bestaetigte fruehere Cover-Slots bleiben unveraendert auf Disk erhalten.
7. Speichere neue Patterns sofort an `cover-patterns.md`, sobald der User die dauerhafte Aufnahme bestaetigt. Pattern-Format bleibt strikt wie oben beschrieben.
8. Geh erst zum naechsten Cover-Slot weiter, wenn der aktuelle Slot inhaltlich bestaetigt ist.
9. Bevor du den naechsten Slot draftest, zeig dem User den unmittelbar vorherigen bestaetigten Absatz unveraendert als Referenzkontext.

Wenn `cover-patterns.md` leer oder fehlend ist, starte direkt bei den drei Alternativen pro Slot.

## `cv.tex` schreiben

Der Build-Pfad liest den aktuellen Slot-Map-Stand aus `<application-folder>/cv.tex` und substituiert die Bodies in `cv_template.tex` (das im Package liegt). Nach dem vierten bestaetigten Cover-Slot liegt damit bereits der vollstaendige Draft auf Disk.

## Build-Aufruf

Rufe das Build-Skript `application-pipeline compile-cv <application-folder>` auf. Vorher sicherstellen, dass das `application-pipeline`-CLI auf `PATH` ist. Erfolg: `cover.pdf`, `resume.pdf` und `combined.pdf` landen im Application-Ordner. Fehler: das Command schreibt den relevanten Grep aus dem Job-Log nach `stderr` und beendet non-zero.

Bei Non-Zero-Exit: dem User in Prosa sagen, dass der Compile fehlgeschlagen ist, mit dem stderr verbatim als Anhang. Dann stopp. Versuche den Strip-Down-Loop nicht — der behandelt nur Overflow, keine syntaktischen LaTeX-Fehler und keine Slot-Map-Parser-Fehler (fehlende/extra Slots).

## Seiten-Overflow-Loop

Nach erfolgreichem Build: Seitenzahlen von `cover.pdf` und `resume.pdf` lesen.

- **Resume-Overflow** bleibt automatisch und läuft nach dem untenstehenden Strip-Down-Loop.
- **Cover-Overflow** geht in **Interactive Cover Shortening** statt in einen rein automatischen Strip-Down.

## Interactive Cover Shortening

Wenn `cover.pdf` zu lang ist:

1. Identifiziere die verdaechtigen Cover-Prosa-Slots.
2. Erzeuge voll ausformulierte verkuerzte Absatz-Varianten fuer den Engpass, bevorzugt eine Variante pro betroffenem Slot.
3. Zeige dem User die verkuerzten Varianten **vollstaendig** in Prosa; keine diff-Fragmente, keine blossen Streichlisten.
4. Lass den User genau **eine** Variante auswaehlen.
5. Schreibe **nur** die vom User gewaehlte verkuerzte Variante nach `<application-folder>/cv.tex`.
6. Baue erneut und pruefe die Seitenzahl neu.
7. Wiederhole nur falls weiter Cover-Overflow besteht.

Wichtig:

- Dieser Post-Build-Shortening-Loop schreibt **nie** nach `cover-patterns.md`, auch dann nicht, wenn eine gekuerzte Fassung gut klingt.
- Resume-Overflow bleibt automatisch; nur Cover-Prosa wird interaktiv verkuerzt.
- Existing Patterns werden in dieser Phase nicht als Bibliotheks-Entscheidung behandelt; es geht nur um die aktuelle `cv.tex`.

## Erfolgs-Report

Wenn der Loop konvergiert: in Prosa eine kurze Zusammenfassung — Pfad zum Application-Ordner, die drei generierten PDFs mit finalen Seitenzahlen, Anzahl Resume-Strip-Down-Iterationen, ob und wie oft **Interactive Cover Shortening** benoetigt wurde, und dass der Cover-Draft ueber die interaktive Slot-Schleife bestaetigt wurde. Danach bleib im selben `/write-cv`-Run und frage, was als Nächstes geändert werden soll.

## Resident-Loop

Nach dem initialen Erfolgs-Report oder wenn der User im Startup-Check "bestehenden Draft iterieren" wählt: lies `analysis.md`, alle `application-pipeline/user-info/triage-profile/*.md`-Files und `cv.tex` als Slot-Map laut [_shared/SLOT-MAP.md](_shared/SLOT-MAP.md) in den Speicher. `application-pipeline/cv-template/cv_skeleton.tex` wird **nicht** routinemäßig gelesen. Frage den User, was geändert werden soll.

Die Slot-Bodies werden **nicht** unaufgefordert dem User zurückgespiegelt — er kennt seinen Draft. Berührt ein Anliegen einen Slot, wird genau dieser Slot (und nur dieser) im Turn-Output sichtbar.

Dann schleifen:

1. **Snapshotte** `cv.tex` (falls vorhanden), `analysis.md` und die drei `user-info/triage-profile/*.md`-Files im Speicher, bevor irgendeine Änderung dieses Turns angewandt wird — auch in Analysis-only-Turns, weil ein einzelner Turn beide Buckets mischen kann. Das ist die Revert-Basis für „Compile-Fehler mitten in der Iteration".
2. **Lies das Anliegen** des Users (eine Nachricht kann mehrere distinkte Feedback-Teile enthalten; vertraue der eigenen Dekomposition).
3. **Erkenne Exit per Prosa-Intention.** Wenn der User in irgendeiner Form signalisiert, dass er fertig ist („passt", „so lassen", „fertig", leerer Input): geh in den *Exit*-Schritt. Bei Mehrdeutigkeit kurz nachfragen, ob als final zu verstehen, statt einen Token zu erzwingen.
4. **Pro Anliegen:** Per-Concern-Flow unten.
5. **Falls `cv.tex` in diesem Turn berührt wurde:** einmal kompilieren am Ende des Turns. Bei Erfolg den Seiten-Overflow-Strip-Down-Loop ausführen. Wurde `cv.tex` nicht berührt: kein Build, kein Strip-down.
6. Re-prompt nach dem nächsten Anliegen.

## Per-Concern-Flow

1. **Klassifiziere** in einen von vier Buckets — die Quelle des Anliegens entscheidet:
   - **Style-Signal** — Stimm-/Phrasing- oder Cover-Strategie-Muster, das auch für künftige Drafts gelten soll. Sub-Routing entscheidet die Ziel-Datei und Sektion:
     - *Regel-Form, Phrasing* ("vermeide X", "X klingt nach LLM", "mach immer Y"): formuliere die Regel direkt im jeweiligen Absatz als knappe Do-/Don't-Korrektur.
     - *Strategie-Form, Inhalt/Bogen/Beleg pro Slot* ("Bootcamp nicht im Brief", "Sinnstiftungs-Pivot passt hier nicht", "nur ein Anker pro Slot", "Meta-Beleg nicht doppelt"): passe den aktuellen Draft direkt an, ohne separate Regel-Datei.
     - *Vorbild-Form* ("der Ton von Brief X war besser", "schreib mehr wie Vorbild N", "dieser Snippet trägt das Muster Y"): nutze das Vorbild nur als lokale Stilreferenz im aktuellen Draft, nicht als persistente Datei.
     - **Hard ban:** keine Negativ-Exemplare und keine separate Stilbibliothek anlegen. Wenn ein konkreter Failure-Satz oder schlechter KI-Draft aus dem aktuellen Schreiben Auslöser ist, abstrahiere zur Regel und verwerfe den Beispiel-Satz.
   - **Fakt-über-mich** — Berufsweg, Skill, Präferenz, Identitäts-Signal, das über dieses Listing hinaus verallgemeinert. Ziel: eines von `candidate-profile.md` / `gate-criteria.md` nach dem Profil-Routing oben.
   - **Position-spezifisch** — nur für dieses Listing relevant, betrifft den CV-Draft. Ziel: nur `cv.tex`.
   - **Analysis-Fakt-Korrektur** — der User adressiert eine Stelle, die ausschließlich in `analysis.md` lebt (Tailoring-Hook, Why-Apply-Bullet, Fit-Aussage) und die faktisch falsch ist. Ziel: `analysis.md` **plus** Root-Cause in `user-info/triage-profile/*.md`. Siehe eigener Sub-Flow unten.

2. **Wenn die Klassifizierung mehrdeutig ist** oder eine Profil-Datei auf schwachem Signal umgeschrieben würde: kurze Grilling-Sub-Session im Geist von `/grill-me` — eine Frage pro Turn, Deutsch, jeweils mit empfohlener Antwort, bis Änderung und Zielort klar sind. Für eindeutig Position-spezifisches Feedback überspringen — Grilling ist Eskalation, nicht Default.

3. **Wende die Änderung an** — inline im selben Turn, nicht batchen.
   - **Position-spezifisch** → identifiziere den/die betroffenen Slot(s) in der Slot-Map (siehe „Slot-Identifikation" unten) und editiere ausschließlich diese(n) Body.
   - **Style-Signal** → direkt im betroffenen Slot oder im laufenden Draft umsetzen; keine separate Stil-Datei mehr.
   - **Fakt-über-mich** → die geroutete `user-info/triage-profile/*.md`-Datei nach dem Profil-Routing oben schreiben. Sichtbare Konsequenzen in die betroffenen Slot-Bodies von `cv.tex` mit-einarbeiten (falls `cv.tex` existiert).
   - **Analysis-Fakt-Korrektur** → Sub-Flow unten.

4. **Slot-Identifikation (für `cv.tex`-Edits).** Aus dem Wortlaut des Feedbacks und dem aktuellen Body-Inhalt ableiten, welche(r) Slot(s) betroffen sind. Mehrere Slots in einem Anliegen sind erlaubt (z.B. „die Anrede passt nicht zum Empfänger" → `recipient_name` + `opening`). Ist nicht klar, welcher Slot gemeint ist oder wie das Feedback im Kontext des Slot-Zwecks zu interpretieren ist: lies **gezielt** den passenden Slot-Block aus `application-pipeline/cv-template/cv_skeleton.tex` (Header + die `% …`-Guidance-Kommentare unmittelbar danach) und nutze die Guidance, um Intent und Zielort aufzulösen. Nur lesen, nicht im Voraus laden, und nicht den ganzen Skeleton-Inhalt — nur den/die relevanten Block(s).

5. **`cv.tex` zurückschreiben.** Nach allen Slot-Edits dieses Turns: die gesamte Slot-Map mit unveränderten Headern und in unveränderter Reihenfolge serialisieren und nach `<app_dir>/cv.tex` schreiben. Alle kanonischen Slots müssen präsent bleiben — auch unveränderte. Nach dem Schreiben muss `cv.tex` mit dem Slot-Map-Parser sauber parsen.

6. **Content-Pool-Item-Feedback.** Wenn das Feedback ein konkretes Erfahrungs-Item ist, das für künftige CVs verfügbar sein soll: Änderung für *dieses* Listing nur auf `cv.tex` anwenden und den User einmal in Prosa darauf hinweisen, dass dauerhafte Pool-Pflege manuell in `application-pipeline/user-info/cv/content_pool.tex` passieren muss.

## Sub-Flow: Analysis-Fakt-Korrektur

Symptom in `analysis.md` und Root-Cause in `user-info/triage-profile/*.md` werden **gemeinsam** behandelt — nie nur das eine.

1. **Symptom lokalisieren.** Finde die konkrete Stelle in `analysis.md` (Hook, Why-Apply-Bullet, Fit-Satz), die der User adressiert.
2. **Root-Cause identifizieren.** Welche Aussage in welcher `user-info/triage-profile/*.md` hat zu dieser Stelle geführt? Meistens offensichtlich aus dem Inhalt (z.B. „pycastle-Konsument" als Hook → entstand aus der `application-pipeline`-Zeile in `candidate-profile.md`). Zielort nach dem Profil-Routing oben bestimmen.
3. **Wenn die Root-Cause nicht eindeutig zuordenbar ist:** Grilling-Sub-Session, bis Quelle und Fix klar sind.
4. **Edge-Case: keine Root-Cause in `user-info/triage-profile/`.** Wenn das Grilling ergibt, dass keine Aussage in `user-info/triage-profile/` den Fehler erklärt (z.B. `/analyse-listing` hat einen Listing-Inhalt halluziniert, der so gar nicht im Stellentext stand): nur `analysis.md` korrigieren und in Prosa explizit sagen „keine Root-Cause in `user-info/triage-profile/` identifiziert — vermutlich Halluzination beim Analyse-Lauf". Kein erzwungenes `user-info/triage-profile/`-Edit.
5. **Beide Edits anwenden** — `analysis.md` und (sofern vorhanden) die geroutete `user-info/triage-profile/*.md`-Datei schreiben.
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

- `<application-folder>/cv.tex` (Format: Slot-Map laut [_shared/SLOT-MAP.md](_shared/SLOT-MAP.md))
- `application-pipeline/user-info/cv/cover-patterns.md` (nur fuer signifikante neue, vom User bestaetigte Cover Paragraph Patterns waehrend des Haupt-Drafting-Loops oder auf expliziten User-Wunsch)
- `<application-folder>/analysis.md` (ausschließlich im Per-Concern-Bucket *Analysis-Fakt-Korrektur*)
- `application-pipeline/user-info/triage-profile/*.md`

`cover.pdf`, `resume.pdf`, `combined.pdf` werden vom `compile-cv`-Command geschrieben, nicht vom Skill. Alles andere im Repo ist read-only.
</hard-rules>

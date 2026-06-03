# Universalregeln

[_shared/CONVENTIONS.md](application-pipeline/agent-skills/_shared/CONVENTIONS.md)

# Aufgabe

Analyse ein Listing Schritt fuer Schritt mit dem User. Das Ziel ist ein Einstieg-plus-Begruendung pro Absatz fuer `analysis.md`.

## 1. Listing bestätigen

Extrahiere `Company` und `Role` aus dem gewaehlten Listing per eigenem Lesen. Das Ordner-Datum ist immer **heute**, unabhaengig vom `posted_date` im Body.

Baue den Ordner-Slug nach folgenden Regeln: 

<slug-rules>
1. Strippe Gender-Marker: `(m/w/d)`, `(m/f/d)`, `(d/w/m)`, `(w/m/d)` und Aequivalente.
2. Strippe Trailing-Location-Segmente: alles nach dem letzten `-` / `--` / ` - `, falls der Schwanz nach einem Ort aussieht.
3. Transliteriere deutsche Umlaute: `ae`, `oe`, `ue`, `ss` und grossgeschriebene Varianten.
4. Ersetze jeden Lauf von Non-`[A-Za-z0-9]` durch ein einzelnes `-`.
5. Strippe fuehrende/abschliessende `-`.
6. Trunkiere bei 40 Zeichen (am letzten `-` vor dem Limit schneiden).
7. Grossschreibung beibehalten.
</slug-rules>

Bestaetige Company, Role und resultierenden Pfad (`application-pipeline/applications/<today>-<Company-slug>_<Role-slug>/`) mit dem User. Erlaube Overrides fuer Company und Role; bei Aenderungen neu sluggen und erneut bestaetigen lassen.

## 2. Grilling-Workflow

Der Workflow ist inkrementell. Es gibt insgesamt 4 Turns, einen pro Absatz:

<Absätze>
1. **intro:** Der persönliche Einstieg: warum diese Rolle, warum jetzt, mit dem stärksten Hook.
2. **bridge:** Die Überleitung vom Motiv zur fachlichen Passung.
3. **evidence:** Die konkreten Belege und Anekdoten für Resonance- und Capability-Hooks.
4. **closing:** Der Schluss: Pull-Fit, Zusammenarbeit, Motivation für den Wechsel und Gesprächseinstieg.
</Absätze>

### Absatz Loop

Pro Absatz gibt es eine Grilling-Schleife mit mehrere Grilling Turns.

<per-turn-flow>
Pro Turn in der Grilling-Schleife, in dieser Reihenfolge. Grille unermüdlich so lange, bis du und der Nutzer ein gemeinsames Verständnis haben:

1. **Formuliere eine Frage. Extrahiere mindestens drei verschiedene Antwortvorschläge aus `candidate-profile.md` und gib eine Empfehlung für die Antwort aus.**
2. **Lies die User-Antwort.**
3. **Schreibe Profil-Updates**: harte Domain-/Ausschluss-Signale nach `gate-criteria.md`, Identitäts-/Werte-/Präferenz-Signale nach `candidate-profile.md`.
</per-turn-flow>

4. **Session-Ende:** Schritte 1-3 muessen abgeschlossen sein, bevor Schritt 4 emittiert wird.
4.1. **Update `analysis.md`:** fuehre den aktuell fertigen Absatz direkt in `analysis.md` ein. Er gilt jetzt als final.
4.2. **Vergleiche die Antwort mit den bestehenden Triage-Profil-Bullets** und extrahiere wenn möglich verallgemeinerbare Signale fuer `gate-criteria.md` und `candidate-profile.md` - auch wenn sie bestehende Bullets vertiefen, differenzieren oder korrigieren, nicht nur wenn sie net-new sind.
5. **Starte eine neue Grilling schleife** für den nächsten Absatz

### Form von `analysis.md`

`analysis.md` wird im Verlauf sofort ergaenzt, nicht erst am Ende. Pfad: `application-pipeline/applications/<today>-<Company-slug>_<Role-slug>/analysis.md`.

<analysis-template>
# Job-Listing

## {Company} - {Title}

{neutrale Zusammenfassung relevanter Listing-Punkte - knapp, nicht wertend}

## Link
{URL zum Listing}

---

```md
## intro
Einstieg: ...
Begruendung: ...

## bridge
Einstieg: ...
Begruendung: ...

## evidence
Einstieg: ...
Begruendung: ...

## closing
Einstieg: ...
Begruendung: ...
```
</analysis-template>

### Schreib-Whitelist

<hard-rules>
Dieser Skill schreibt ausschliesslich in:

- `application-pipeline/applications/<today>-<slug>/analysis.md`
- `application-pipeline/user-info/triage-profile/gate-criteria.md`
- `application-pipeline/user-info/triage-profile/candidate-profile.md`
</hard-rules>

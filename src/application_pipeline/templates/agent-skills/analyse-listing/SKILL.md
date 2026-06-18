---
name: analyse-listing
description: Fragt den Nutzer zu einer konkreten Stellenausschreibung. Wird aktiviert, wenn der Nutzer /analyse-listing aufruft.
---

# Universalregeln

[_shared/CONVENTIONS.md](../_shared/CONVENTIONS.md)

<write-rules>
Dieser Skill schreibt ausschliesslich in:

- `application-pipeline/user-info/triage-profile/gate-criteria.md` -> Informationen zu den Präferenzen und Interessen der Nutzer:in
- `application-pipeline/user-info/triage-profile/candidate-profile.md` -> Informationen zum Profil der Nutzer:in
</write-rules>

# Aufgabe

<what-to-do>

Befrage mich ununterbrochen zu der Stellenausschreibung, bis wir eine gemeinsame Sicht darauf haben, wieso ich mich auf diese bewerben möchte. Geh jeden Zweig des Entscheidungsbaums durch und kläre Abhängigkeiten schrittweise. Gib zu jeder Frage eine konkrete Empfehlung.

Stelle die Fragen einzeln und warte auf Feedback, bevor du mit der nächsten fortfährst.

Wenn eine Frage über `gate-criteria.md` oder `candidate-profile.md` beantwortbar ist, erforsche dazu zuerst diese Dateien statt zu raten.
</what-to-do>

## Während der Sitzung

### Abgleich gegen das Glossar

Wenn der Nutzer eine Information nutzt, der mit einem bestehenden Eintrag in `gate-criteria.md` oder `candidate-profile.md` kollidiert, weise sofort darauf hin.
Beispiel: "Dein Profil definiert `Wohnort` als X, du meinst aber scheinbar Y. Was ist richtig?"

### Unklare Sprache präzisieren

Wenn die Nutzerin eine vagen oder überladene Information nutzt, schlage einen präzisen kanonischen Begriff vor.
Beispiel: "Du sagst `Ort` — meinest du den Wohnort oder den Arbeitsort? Das sind unterschiedliche Dinge."

### `gate-criteria.md` und `candidate-profile.md` direkt aktualisieren

Wenn eine Information geklärt ist, aktualisiere `gate-criteria.md` oder `candidate-profile.md` sofort im selben Schritt. Keine Sammelupdates am Ende — Einträge direkt beim Auftauchen erfassen.

`gate-criteria.md` und `candidate-profile.md` sollen keine Stellenspezifischen Details enthalten. Behandle sie nicht als konkrete Bewerbungsdateien; sie sind ausschließlich Glossare.

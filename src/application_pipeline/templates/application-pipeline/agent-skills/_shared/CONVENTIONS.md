# Skill conventions

<path-convention>
Alle Pfade sind **CWD-relativ**. Claude Code läuft mit dem Repo-Root als primärem Arbeitsverzeichnis und wechselt während der Session nicht via `cd` — `application-pipeline/` ist daher ein stabiler Präfix. Konstruiere Pfade nicht relativ zu etwas anderem: kein `git rev-parse`, keine Upward-Walks, keine absoluten Pfade.
</path-convention>

<hard-rules>
- Sämtliche User-Ausgabe ist auf **Deutsch**. Framework-Fehlermeldungen dürfen Englisch bleiben.
- Antworten an den User sind in eigenen Worten formuliert, nicht der Skill-File-Body verbatim.
- Skills werden ausschließlich durch User-Invocation gestartet — andere Skills nie automatisch nachziehen.
</hard-rules>

<path-convention>
Alle Pfade sind **CWD-relativ**. Claude Code läuft mit dem Repo-Root als primärem Arbeitsverzeichnis und wechselt während der Session nicht via `cd` — `application-pipeline/` ist daher ein stabiler Präfix. Konstruiere Pfade nicht relativ zu anderen Orten: kein `git rev-parse`, keine Navigation nach oben im Verzeichnisbaum, keine absoluten Pfade.
</path-convention>

<hard-rules>
- Sämtliche User-Ausgabe ist auf **Deutsch**. Framework-Fehlermeldungen dürfen Englisch bleiben.
- Skills werden ausschließlich durch Nutzeraufruf gestartet — andere Skills nie automatisch nachziehen.
- Nutze nur die Workspace-Datei `application-pipeline/*`; Template-Mirrors unter `src/.../templates` sind nicht die Quelle der Wahrheit.
</hard-rules>

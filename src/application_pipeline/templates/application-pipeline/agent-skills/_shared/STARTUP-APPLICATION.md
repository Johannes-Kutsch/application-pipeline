# Startup-Check: Application-Build

<startup-checks>
Führe zuerst alle Checks aus [STARTUP-TRIAGE.md](STARTUP-TRIAGE.md) aus. Dann zusätzlich:

Verifiziere, dass `application-pipeline/user-info/cv/` zusätzlich zu den Triage-Files diese enthält:

- `facts.tex`
- `content_pool.tex`
- `signature.png`
- `profile.png`

Verifiziere, dass `application-pipeline/cv-template/cv_skeleton.tex` relativ zur CWD existiert.

Verifiziere, dass `analysis.md` im aufgelösten Application-Ordner existiert. Fehlt sie: dem User sagen, dass `/analyse-listing` zuerst laufen muss, dann stopp.

Bei einem Miss in `user-info/cv/` oder beim Skelett: dem User in eigenen Worten sagen, welche Datei fehlt und dass `application-pipeline init --refresh` sie initialisiert. Dann stopp.
</startup-checks>

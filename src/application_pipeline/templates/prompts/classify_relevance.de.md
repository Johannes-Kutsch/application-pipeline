Du bist ein Relevanzklassifikator für Stellenanzeigen. Der Kandidat ist ein angewandter
KI-Generalist mit einem MLE-/AI-Engineer-Kern (Python, scikit-learn, TensorFlow, MLflow,
Docker, Kubernetes, FastAPI) und vorheriger Senior-Erfahrung in der Spieleentwicklung (Unity,
C#, ~7 Jahre). In-Domain sind klassische MLE-/MLOps-/Data-Science-/Applied-Science-Rollen,
Reinforcement Learning und Game-AI, industrielle oder angewandte Forschung (Bosch Corporate
Research, Fraunhofer, ML-Forschungsgruppen großer Unternehmen), Data Engineering mit
Modellierungsanteil, OSINT-Analytics-Tooling, Controlling-/FP&A-Rollen mit Spielraum zum Aufbau
eigener Analyse-Tools, EdTech / Serious Games / Public-Sector-KI, Platform-Engineering- und
Internal-Tooling-Rollen sowie reine Game-Dev-Stellen (Unity / C# ohne ML) — der Kandidat hat
Senior-Tiefe in der Spielebranche und ist für diese Schiene weiterhin offen.

Außerhalb der Domain: Vertrieb, Recruiting, reine Führungspositionen ohne technische Tiefe,
Marketing / Content / HR / Recht / Buchhaltung, Handwerks- und Lagerberufe sowie jede Stelle
ohne Software-, Daten- oder ML/KI-Anteil.

Lies für jedes Element den Titel und die Beschreibung. Setze in_domain auf true, wenn der
Kandidat sich realistisch bewerben könnte, oder auf false, wenn die Stelle außerhalb des oben
beschriebenen Bereichs liegt.

Antworte mit einem JSON-Array, einem Eintrag pro Eingabeelement, alle ids beibehaltend:
[
  {{"id": "<id>", "in_domain": true}},
  {{"id": "<id>", "in_domain": false}}
]

Zu klassifizierende Stellenanzeigen:

{ITEMS}

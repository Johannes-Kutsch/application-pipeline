# Seiten-Overflow-Strip-Down-Loop

<strip-down>
Pro Iteration:

1. **Cover-Overflow** (`cover.pdf` > 1 Seite): Prosa in den vier Cover-Paragraph-Slots in fixer Reihenfolge straffen: `cover_fit` → `cover_pivot` → `cover_intro` → `cover_closing`. Pro Iteration den nächsten Slot in der Reihe straffen. Absatz-Anzahl nicht ändern; nur Prosa komprimieren.
2. **Resume-Overflow** (`resume.pdf` > 2 Seiten): den geringsten Beitrag aus den drei Resume-Slots (`resume_berufserfahrung`, `resume_ausbildung`, `resume_projekte`) entfernen. Wenn die `group:`-Metadaten eines Items eine kürzere Variante anbieten, ist das Tauschen-statt-Droppen erlaubt (und meist die mildere Wahl). Ein `always: true`-Item darf nur gedroppt werden, wenn es keine kürzere Variante in seiner `group:` mehr gibt.
3. Schreibe `cv.tex` mit den Änderungen neu — die Slot-Map-Form bleibt erhalten, nur die betroffenen Slot-Bodies werden ersetzt. Rufe das Build-Skript erneut auf.
4. Lies die Seitenzahlen erneut. Falls beide im Budget: erfolgreich aus dem Loop. Sonst iterieren.

Keine Iterations-Obergrenze. Stopp wenn:

- Beide PDFs im Budget — Erfolgspfad.
- Resume noch über Budget und nur noch `always: true`-Items bleiben (ohne kürzere `group:`-Variante) — Inkonvergenz.
- Cover noch über Budget und alle vier Absätze auf Minimum gestrafft — Inkonvergenz.

Bei Inkonvergenz: **nicht** raisen. In Prosa reporten, was im finalen `cv.tex` steht, dass die Budgets nicht erreicht wurden, mit den aktuellen Seitenzahlen, und dem User `/iterate-cv` als nächsten Schritt vorschlagen (mit vollqualifiziertem Pfad).
</strip-down>

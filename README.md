# uhppoted-dashboard

Datastore + ingesta + API + UI para gestion de controladores, historico de eventos y KPIs de acceso.
Solo stdlib de Python 3.8. Tests: `python3 -m unittest discover -s tests -v`.

## Estado Plan 1 (foundation)
Datastore + ingesta operativos. `doors-ingest.timer` cada 10 min. Backfill Palmetto desde 2026-01-01; Teq = ventana del poller (cosecha profunda diferida al edge box).

# uhppoted-dashboard

Datastore + ingesta + API + UI para gestion de controladores, historico de eventos y KPIs de acceso.
Solo stdlib de Python 3.8. Tests: `python3 -m unittest discover -s tests -v`.

## Estado Plan 1 (foundation)
Datastore + ingesta operativos. `doors-ingest.timer` cada 10 min. Backfill Palmetto desde 2026-01-01; Teq = ventana del poller (cosecha profunda diferida al edge box).

## Supuestos de datos y notas operativas (Plan 1)

- **Timestamps = local naive -05 (Colombia).** Verificado 2026-07-22: los relojes internos de las 5 placas y el poller de Teq usan hora local -05; Palmetto trae sufijo `-05` (se recorta), Teq viene sin sufijo. Todos los `timestamp` en `events` son **hora local -05 sin zona**. KPIs cross-sede (Plan 3) pueden comparar directo. Si en el futuro una placa quedara en otra zona, normalizar antes de agregar.
- **Contrato del seam de ingesta:** `device_id` e `idx` DEBEN ser enteros. El futuro colector edge-box-push (spec §6) debe enviarlos como int, no string, o los PK/cursores no casarán. Coercer `int()` en ese colector cuando se construya.
- **Durabilidad de Teq acotada por el uptime del timer.** La ingesta de Teq lee la ventana rolling ~8 días del poller (`teq-events.json`). Si `doors-ingest.timer` estuviera caído más de ~8 días, los eventos Teq de la brecha se pierden antes de ingerirse (hasta la cosecha profunda del edge box). Palmetto NO se afecta: su histórico completo persiste en `events.json`.
- **Plan 2 (API lectora):** al agregar un lector concurrente sobre `doors.db`, activar `PRAGMA journal_mode=WAL` para evitar "database is locked" con el writer de ingesta.

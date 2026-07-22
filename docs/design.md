# Diseño — Gestión de controladores, histórico de eventos y dashboard de accesos (uhppoted AZC)

**Fecha:** 2026-07-22
**Server:** `azc.com.co` = `192.168.12.25` (LAN, /22), NAT `186.145.239.174`
**Proyecto base:** uhppoted access control — panel httpd + `schedule-manager` (:8446) + UI `/schedules/`
**Estado del sistema al diseñar:** 5 placas responden 1×1 (Palmetto `222451671` + 4 Teq vía Tailscale), 352 tarjetas, servicios `active`, uptime 8 semanas.

---

## 1. Objetivo

Tres capacidades nuevas sobre el stack existente, sin stack pesado nuevo:

1. **Gestión completa de todas las placas** (incluidas las 4 de Tequendama que hoy no aparecen en el tab System nativo): editar nombre, estado 1×1, sincronizar reloj, abrir puerta, agregar/quitar tarjetas por placa, editar parámetros de red.
2. **Histórico de eventos consultable por rango de fechas** (no tiempo real), unificado para las 5 placas, con export.
3. **Dashboard de accesos por sede**: promedio de primera entrada / última salida por usuario, asistencia, picos y demás KPIs derivables.

## 2. Contexto técnico que condiciona el diseño (investigado en el server)

- **Nombres de placa NO viven en hardware** → viven en `controllers.json`/`doors.json`/`uhppoted.conf`/`CONTROLLERS_META`. Editar nombre = editar metadata del server.
- **Palmetto:** `events.json` tiene ~70k eventos, dic-2022 → hoy, **con campo `direction`** (1=entra / 2=sale). ~97% pasan por Portería.
- **Tequendama:** el poller (`teq-events.json`) solo retiene **3000 eventos (~8 días)** y **NO trae `direction`**. Los **buffers internos de las placas** guardan decenas de miles c/u (índices hasta 82k–321k) → cosechables por `get-events`/`get-event`.
- **Identidad tarjeta→persona NO disponible:** solo 2/352 tarjetas con nombre; no hay BD de empleados en este server (las BD son Nextcloud/WordPress/mail). **Se poblará a futuro desde otras BD.**
- **`reason=1`** = acceso concedido (la mayoría); `reason=20` = apertura por botón (ruido para KPIs de asistencia).

## 3. Decisiones tomadas (brainstorming 2026-07-22)

| # | Tema | Decisión |
|---|------|----------|
| D1 | Identidad tarjeta→persona | Diseñar **"listo para nombres"**: tabla `card_persons` con `name/sede/rol` nullable. KPIs hoy por número de tarjeta; importador futuro puebla desde las BD que tendrán el dato. Sin rehacer nada al llegar los nombres. |
| D2 | Entrada/salida | Palmetto usa `direction` real. Teq usa heurística **primer swipe del día = llegada, último = salida** por tarjeta (no hay `direction` ni lector dedicado). |
| D3 | Alcance gestión de placas | **Completa**: nombre, estado, sync reloj, abrir puerta, add/remove tarjeta por placa, parámetros de red. |
| D4 | Backfill / almacenamiento | **SQLite local**, backfill **desde 2026-01-01**, acumula hacia adelante. |
| D5 | Enfoque | **Approach A** (todo sobre stack existente) **refinado** con colectores enchufables + endpoint `/ingest` idempotente. |
| D6 | Edge box Linux en Teq (Koichi lo hará en ~2 días) | **Track paralelo opcional.** El diseño es compatible con o sin él: si el Linux es relay → cero cambios; si evoluciona a edge poller → swap de un colector + POST al endpoint idempotente que ya existe. |

## 4. Arquitectura

```
  Palmetto events.json ──►┌──────────────────────────┐
  Teq boards (Tailscale   │ doors-ingest.service     │──► SQLite
    CLI, hoy)          ──►│ colectores enchufables:  │  /var/uhppoted/
  Edge Linux (mañana)     │  • palmetto              │   analytics/doors.db
    POST /ingest       ──►│  • teq-tailscale         │
                          │  • teq-edge-push (futuro)│
                          └────────────┬─────────────┘
                          schedule-manager (:8446) + módulos nuevos
                           API: /events /kpis /controllers/* /ingest
                          UI /schedules/: tabs Controladores·Eventos·Dashboard
```

**Principio rector:** ingesta desacoplada del almacenamiento/análisis. Contrato estable = esquema `events` + endpoint `/ingest` idempotente `(device_id, index)`. Cambiar de dónde/cómo se leen los eventos de Teq no toca nada aguas abajo.

### Componentes y responsabilidades

- **`doors-ingest.service`** (systemd nuevo, ~cada 10 min): orquesta colectores y hace upsert a SQLite. Un colector = una fuente. NO expone HTTP.
- **SQLite `doors.db`**: único dueño del histórico. Gestionable (archivo local, respaldable).
- **`schedule-manager` (:8446) + módulos nuevos**: API de lectura (eventos/KPIs), CRUD de controladores, y `/api/ingest/events`. Sirve la UI.
- **UI `/schedules/index.html`**: 3 tabs nuevos/actualizados.

## 5. Modelo de datos (SQLite)

- **`events`** — PK `(device_id, index)` (idempotencia). Columnas: `timestamp TEXT, card INTEGER, door INTEGER, door_name TEXT, granted INTEGER, reason INTEGER, direction INTEGER NULL, event_type INTEGER, sede TEXT, source TEXT, ingested_at TEXT`. Índices por `timestamp`, `card`, `sede`.
- **`controllers`** — `serial PK, name, sede, ip, listener, network_json, added` → verdad editable de nombres/metadata; el CRUD sincroniza a `controllers.json`/`doors.json`/`uhppoted.conf`.
- **`doors_meta`** — `device_id, door, label, entry_exit_role NULL` (PK `(device_id, door)`). `entry_exit_role` reservado por si luego se clasifican lectores.
- **`card_persons`** — `card PK, name NULL, sede NULL, rol NULL, updated` → campo "listo para nombres"; blanco del importador futuro.
- **`ingest_state`** — `source, device_id, last_index, last_run` (PK `(source, device_id)`); cursores de ingesta.

## 6. Ingesta y compatibilidad futura

- **Colector `palmetto`**: lee `events.json` (trae `direction`), filtra `>= 2026-01-01`, upsert. Cursor por `index` en `ingest_state`.
- **Colector `teq-tailscale`**: por placa, `get-events` (first/last), `get-event` incremental desde cursor con **reintentos de warmup**. **Pausa `teq-keepalive` durante el bulk** (patrón del publish worker) para no contender. Teq no trae `direction` → `direction=NULL`.
- **`POST /api/ingest/events`**: upsert por lote idempotente `(device_id, index)`. Hoy uso interno; mañana el **edge Linux** hace POST de sus lotes. Auth-protegido (§9).
- **Colector `teq-edge-push` (futuro, NO se construye ahora)**: cuando exista el edge box, el server deja de pull-ear Teq y recibe push. Cambio localizado; esquema/API/UI intactos.

## 7. Objetivo 1 — Tab Controladores editable (gestión completa)

Por placa (las 5), sobre `schedule-manager`:
- **Editar nombre** de placa y etiquetas de puerta → `controllers`/`doors_meta` + sync a JSON/conf.
- **Estado 1×1** con indicador explícito **OK / offline** (corrige el `status:None` actual).
- **Sincronizar reloj** (`set-time`).
- **Abrir puerta** remota (`open-door`), con confirmación.
- **Agregar/quitar tarjeta** por placa (`put-card`/`delete-card`, `--with-pin` para Teq).
- **Editar parámetros de red** (IP/gateway/listener), con **confirmación doble** (riesgo de dejar la placa inalcanzable).

Endpoints: `GET /api/controllers`, `PUT /api/controllers/<serial>`, `POST /api/controllers/<serial>/{sync-time,open-door,cards}`, `PUT /api/controllers/<serial>/network`.

## 8. Objetivo 2 — Tab Eventos con filtro por fechas

- `GET /api/events?from=&to=&device=&card=&door=&granted=&page=` sobre SQLite.
- Filtros: rango de fechas, sede, placa, puerta, tarjeta, concedido/negado. Orden desc por timestamp. Paginación.
- **Export CSV** del rango filtrado.

## 9. Objetivo 3 — Tab Dashboard + KPIs

- Gráficos con **Chart.js embebido** (self-contained; la UI se sirve estática, sin CDN externo).
- Por **sede** y **rango**:
  - **Primera entrada / última salida promedio** — Palmetto por `direction` (1=entrada / 2=salida); Teq por heurística primer/último swipe (D2).
  - **Asistencia diaria** (tarjetas únicas/día), **pico horario**, **top puertas**, **tendencia de volumen**, **llegadas tardías** (umbral configurable).
  - Filtro base `granted=1 AND reason=1` (excluye botón reason=20 y ruido).
- KPIs hoy por número de tarjeta; al poblar `card_persons`, por persona sin cambios de código.
- `GET /api/kpis?from=&to=&sede=`.

## 10. Auth + seguridad (cierra pendiente)

Se agregan escrituras potentes (CRUD placas, abrir puerta, `/ingest`) → **se agrega auth a `/schedules/api/`** (hoy sin auth). Validar cookie de sesión de `uhppoted-httpd` o basic-auth. Cierra el hueco de seguridad pendiente y protege lo nuevo.

## 11. Testing (TDD sobre lógica pura)

- **KPIs**: set sintético de eventos → asertar primera-entrada/última-salida, asistencia, pico horario, llegadas tardías.
- **Ingesta idempotente**: doble ingest del mismo lote → sin duplicados (PK).
- **Filtros `/api/events`**: rango/sede/placa/tarjeta correctos.
- **Heurística Teq vs direction Palmetto**: mismos eventos, asertar que cada camino da la llegada/salida esperada.
- **Integración**: ingesta desde `events.json` de muestra → query API devuelve lo esperado.

## 12. Fuera de alcance (YAGNI ahora)

- Edge box Linux en Teq (track paralelo de Koichi; el diseño ya es compatible).
- Clasificación manual de lectores entrada/salida (`entry_exit_role` queda reservado).
- Importador real de nombres (esquema `card_persons` + endpoint quedan listos; el import se cablea cuando exista la BD fuente).
- Herramienta BI externa (Metabase/Grafana) — descartada por el box de 8GB.

## 13. Riesgos / notas

- **Contención con `teq-keepalive`** durante cosecha bulk → el colector lo pausa/reanuda.
- **Warmup DERP en Teq** hasta que haya conexión directa/edge box → reintentos en el colector.
- **Editar red de una placa** puede dejarla inalcanzable → confirmación doble + backup de conf.
- **Box chico (8GB, prod de puertas)** → mantener liviano; ingesta espaciada (~10 min), sin jobs pesados concurrentes con httpd.
- **`load-acl`/Sync del panel** siguen prohibidos post-profiles (regla existente); este diseño no los usa.

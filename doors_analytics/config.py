# doors_analytics/config.py
DB_PATH = "/var/uhppoted/analytics/doors.db"
# Palmetto: eventos por el poller UDP directo (palmetto-events-poller.service), sin panel.
PALMETTO_JSON = "/var/uhppoted/palmetto-events.json"
# origen legacy (panel httpd), conservado para rollback: /var/uhppoted/httpd/system/events.json
EVENTS_JSON = "/var/uhppoted/palmetto-events.json"
TEQ_JSON = "/var/uhppoted/teq-events.json"
SINCE_DATE = "2026-01-01"
PALMETTO_SERIAL = 222451671
TEQ_SERIALS = [225088590, 425036574, 423150802, 223205300]

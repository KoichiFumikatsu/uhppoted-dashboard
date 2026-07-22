# doors_analytics/collectors.py
import json


def _load(path):
    with open(path) as f:
        d = json.load(f)
    return d.get("events", d if isinstance(d, list) else [])


def collect_palmetto(events_json_path, serial, since_date, after_index):
    out = []
    for e in _load(events_json_path):
        if e.get("device-id") != serial:
            continue
        ts = (e.get("timestamp") or "")
        if ts[:10] < since_date:
            continue
        idx = e.get("index")
        if idx is None or idx <= after_index:
            continue
        out.append({
            "device_id": serial,
            "idx": idx,
            "timestamp": ts[:19],
            "card": e.get("card"),
            "door": e.get("door"),
            "door_name": e.get("door-name"),
            "granted": 1 if e.get("granted") else 0,
            "reason": e.get("reason"),
            "direction": e.get("direction"),
            "event_type": e.get("event-type"),
            "sede": "Palmetto",
            "source": "palmetto",
        })
    out.sort(key=lambda x: x["idx"])
    return out


def collect_teq(teq_json_path, after_index_by_serial, since_date):
    out = []
    for e in _load(teq_json_path):
        serial = e.get("device-id")
        cursor = after_index_by_serial.get(serial, 0)
        ts = (e.get("timestamp") or "")
        idx = e.get("index")
        if idx is None or idx <= cursor:
            continue
        if ts[:10] < since_date:
            continue
        out.append({
            "device_id": serial,
            "idx": idx,
            "timestamp": ts[:19],
            "card": e.get("card"),
            "door": e.get("door"),
            "door_name": e.get("door-name"),
            "granted": 1 if e.get("granted") else 0,
            "reason": e.get("reason"),
            "direction": None,
            "event_type": None,
            "sede": "Tequendama",
            "source": "teq",
        })
    out.sort(key=lambda x: (x["device_id"], x["idx"]))
    return out

# tests/test_collectors.py
import json
import os
import tempfile
import unittest
from doors_analytics import collectors


def _write(tmp, obj):
    path = os.path.join(tmp, "e.json")
    with open(path, "w") as f:
        json.dump(obj, f)
    return path


class TestCollectPalmetto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = {"events": [
            {"device-id": 222451671, "index": 10, "timestamp": "2025-12-31 09:00:00 -05",
             "event-type": 1, "door": 1, "direction": 1, "card": 111,
             "granted": True, "reason": 1, "door-name": "S1 Porteria"},   # antes de 2026 -> fuera
            {"device-id": 222451671, "index": 11, "timestamp": "2026-01-02 07:15:00 -05",
             "event-type": 1, "door": 1, "direction": 1, "card": 222,
             "granted": True, "reason": 1, "door-name": "S1 Porteria"},   # ok
            {"device-id": 222451671, "index": 12, "timestamp": "2026-01-03 18:40:00 -05",
             "event-type": 1, "door": 1, "direction": 2, "card": 222,
             "granted": False, "reason": 6, "door-name": "S1 Porteria"},  # ok, granted False
            {"device-id": 423150802, "index": 99, "timestamp": "2026-02-01 10:00:00 -05",
             "event-type": 1, "door": 1, "direction": 1, "card": 333,
             "granted": True, "reason": 1, "door-name": "Teq .150 P1"},   # otro device -> fuera
        ]}
        self.path = _write(self.tmp, self.data)

    def test_filters_by_date_device_and_cursor(self):
        out = collectors.collect_palmetto(self.path, 222451671, "2026-01-01", after_index=0)
        idxs = [e["idx"] for e in out]
        self.assertEqual(idxs, [11, 12])  # 10 (2025) y 99 (otro device) fuera

    def test_cursor_excludes_seen(self):
        out = collectors.collect_palmetto(self.path, 222451671, "2026-01-01", after_index=11)
        self.assertEqual([e["idx"] for e in out], [12])

    def test_normalization(self):
        out = collectors.collect_palmetto(self.path, 222451671, "2026-01-01", after_index=0)
        e = out[0]
        self.assertEqual(e["timestamp"], "2026-01-02 07:15:00")  # tz recortado
        self.assertEqual(e["granted"], 1)
        self.assertEqual(e["direction"], 1)
        self.assertEqual(e["sede"], "Palmetto")
        self.assertEqual(e["source"], "palmetto")
        self.assertEqual(out[1]["granted"], 0)  # False -> 0


class TestCollectTeq(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = {"events": [
            {"device-id": 225088590, "index": 500, "timestamp": "2026-07-14 15:48:53",
             "card": 17059974, "door": 1, "door-name": "Teq .125 P1",
             "granted": True, "reason": 1},
            {"device-id": 425036574, "index": 800, "timestamp": "2026-07-14 16:00:00",
             "card": 999, "door": 1, "door-name": "Teq .12 P1",
             "granted": True, "reason": 1},
        ], "cursor": {}}
        self.path = _write(self.tmp, self.data)

    def test_per_serial_cursor_and_defaults(self):
        after = {225088590: 500}  # ya vimos hasta 500 -> excluye ese; .12 sin cursor -> incluye
        out = collectors.collect_teq(self.path, after, "2026-01-01")
        self.assertEqual([(e["device_id"], e["idx"]) for e in out], [(425036574, 800)])
        e = out[0]
        self.assertIsNone(e["direction"])
        self.assertEqual(e["sede"], "Tequendama")
        self.assertEqual(e["source"], "teq")
        self.assertEqual(e["granted"], 1)

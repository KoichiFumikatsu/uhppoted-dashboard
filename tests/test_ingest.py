# tests/test_ingest.py
import json
import os
import sqlite3
import tempfile
import unittest
from doors_analytics import db, ingest


class TestRunOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pal = os.path.join(self.tmp, "palmetto.json")
        self.teq = os.path.join(self.tmp, "teq.json")
        with open(self.pal, "w") as f:
            json.dump({"events": [
                {"device-id": 222451671, "index": 11, "timestamp": "2026-01-02 07:15:00 -05",
                 "event-type": 1, "door": 1, "direction": 1, "card": 222,
                 "granted": True, "reason": 1, "door-name": "S1 Porteria"},
                {"device-id": 222451671, "index": 12, "timestamp": "2026-01-03 18:40:00 -05",
                 "event-type": 1, "door": 1, "direction": 2, "card": 222,
                 "granted": True, "reason": 1, "door-name": "S1 Porteria"},
            ]}, f)
        with open(self.teq, "w") as f:
            json.dump({"events": [
                {"device-id": 225088590, "index": 500, "timestamp": "2026-07-14 15:48:53",
                 "card": 17059974, "door": 1, "door-name": "Teq .125 P1",
                 "granted": True, "reason": 1},
            ]}, f)
        self.conn = sqlite3.connect(":memory:")
        db.init_db(self.conn)

    def _run(self):
        return ingest.run_once(self.conn, self.pal, self.teq,
                               222451671, [225088590, 425036574], "2026-01-01")

    def test_ingests_and_counts(self):
        counts = self._run()
        self.assertEqual(counts, {"palmetto": 2, "teq": 1})
        total = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(total, 3)

    def test_second_run_is_idempotent(self):
        self._run()
        counts2 = self._run()  # cursores ya avanzados -> nada nuevo
        self.assertEqual(counts2, {"palmetto": 0, "teq": 0})
        total = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(total, 3)

    def test_cursors_advanced(self):
        self._run()
        self.assertEqual(db.get_cursor(self.conn, "palmetto", 222451671), 12)
        self.assertEqual(db.get_cursor(self.conn, "teq", 225088590), 500)

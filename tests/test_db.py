# tests/test_db.py
import sqlite3
import unittest
from doors_analytics import db


class TestInitDb(unittest.TestCase):
    def test_creates_tables_idempotent(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        db.init_db(conn)  # segunda vez no debe fallar
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(
            {"events", "controllers", "doors_meta", "card_persons",
             "ingest_state"}.issubset(names))


# añadir a tests/test_db.py
class TestUpsertEvents(unittest.TestCase):
    def _ev(self, idx):
        return {"device_id": 222451671, "idx": idx,
                "timestamp": "2026-03-01 08:00:00", "card": 17059974,
                "door": 1, "door_name": "S1 Porteria", "granted": 1,
                "reason": 1, "direction": 1, "event_type": 1,
                "sede": "Palmetto", "source": "palmetto"}

    def test_upsert_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        batch = [self._ev(1), self._ev(2)]
        db.upsert_events(conn, batch)
        db.upsert_events(conn, batch)  # segunda vez: sin duplicados
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 2)

    def test_upsert_preserves_fields(self):
        conn = sqlite3.connect(":memory:")
        db.init_db(conn)
        db.upsert_events(conn, [self._ev(5)])
        row = conn.execute(
            "SELECT card, direction, granted, sede FROM events WHERE idx=5"
        ).fetchone()
        self.assertEqual(row, (17059974, 1, 1, "Palmetto"))


if __name__ == "__main__":
    unittest.main()

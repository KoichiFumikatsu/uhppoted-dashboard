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


if __name__ == "__main__":
    unittest.main()

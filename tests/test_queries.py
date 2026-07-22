# tests/test_queries.py
import sqlite3
import unittest
from doors_analytics import db, queries


def _seed(conn):
    db.init_db(conn)
    evs = [
        # device, idx, ts, card, door, door_name, granted, reason, direction, sede, source
        (222451671, 1, "2026-03-01 07:10:00", 111, 1, "S1 Porteria", 1, 1, 1, "Palmetto", "palmetto"),
        (222451671, 2, "2026-03-01 18:30:00", 111, 1, "S1 Porteria", 1, 1, 2, "Palmetto", "palmetto"),
        (222451671, 3, "2026-03-02 08:00:00", 222, 1, "S1 Porteria", 0, 6, 1, "Palmetto", "palmetto"),
        (225088590, 900, "2026-03-02 09:00:00", 111, 1, "Teq .125 P1", 1, 1, None, "Tequendama", "teq"),
    ]
    conn.executemany(
        "INSERT INTO events (device_id, idx, timestamp, card, door, door_name, "
        "granted, reason, direction, sede, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)", evs)
    conn.execute("INSERT INTO card_persons (card, name) VALUES (?,?)", (111, "Juan Perez"))
    conn.commit()


class TestQueryEvents(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def test_no_filters_returns_all_desc(self):
        r = queries.query_events(self.conn, {})
        self.assertEqual(r["total"], 4)
        # orden desc por timestamp: el más nuevo (2026-03-02 09:00) primero
        self.assertEqual(r["rows"][0]["timestamp"], "2026-03-02 09:00:00")

    def test_date_range_inclusive(self):
        r = queries.query_events(self.conn, {"from": "2026-03-01", "to": "2026-03-01"})
        self.assertEqual(r["total"], 2)  # solo los dos del 01

    def test_filter_by_sede_and_card(self):
        r = queries.query_events(self.conn, {"sede": "Tequendama"})
        self.assertEqual(r["total"], 1)
        r2 = queries.query_events(self.conn, {"card": 222})
        self.assertEqual(r2["total"], 1)

    def test_filter_granted(self):
        r = queries.query_events(self.conn, {"granted": "0"})
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["rows"][0]["card"], 222)

    def test_card_name_join(self):
        r = queries.query_events(self.conn, {"card": 111, "page_size": 1})
        self.assertEqual(r["rows"][0]["card_name"], "Juan Perez")
        r2 = queries.query_events(self.conn, {"card": 222})
        self.assertIsNone(r2["rows"][0]["card_name"])

    def test_pagination(self):
        r = queries.query_events(self.conn, {}, page=1, page_size=2)
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["pages"], 2)
        self.assertEqual(r["page"], 1)

    def test_csv_has_header_and_rows(self):
        rows = queries.query_events(self.conn, {})["rows"]
        csv_text = queries.events_to_csv(rows)
        lines = [l for l in csv_text.splitlines() if l.strip()]
        self.assertEqual(lines[0], "timestamp,sede,device_id,door,door_name,card,card_name,granted,reason,direction")
        self.assertEqual(len(lines), 1 + 4)


if __name__ == "__main__":
    unittest.main()

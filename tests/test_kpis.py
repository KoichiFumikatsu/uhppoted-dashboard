# tests/test_kpis.py
import sqlite3
import unittest
from doors_analytics import db, kpis


def _seed(conn):
    db.init_db(conn)
    rows = [
        # Palmetto con direction: card 111 el 2026-03-01 entra 07:00 (dir1) sale 18:00 (dir2)
        (222451671, 1, "2026-03-01 07:00:00", 111, 1, "(P) Porteria", 1, 1, 1, "Palmetto", "palmetto"),
        (222451671, 2, "2026-03-01 18:00:00", 111, 1, "(P) Porteria", 1, 1, 2, "Palmetto", "palmetto"),
        # card 222 entra 09:00 (tarde) sale 17:00
        (222451671, 3, "2026-03-01 09:00:00", 222, 1, "(P) Porteria", 1, 1, 1, "Palmetto", "palmetto"),
        (222451671, 4, "2026-03-01 17:00:00", 222, 1, "(P) Porteria", 1, 1, 2, "Palmetto", "palmetto"),
        # ruido: reason 20 (botón) NO cuenta; granted 0 NO cuenta
        (222451671, 5, "2026-03-01 12:00:00", 111, 1, "(P) Porteria", 1, 20, 1, "Palmetto", "palmetto"),
        (222451671, 6, "2026-03-01 06:30:00", 333, 1, "(P) Porteria", 0, 6, 1, "Palmetto", "palmetto"),
        # Teq sin direction: card 111 el 2026-03-01, swipes 08:00 y 16:00 -> llegada 08:00, salida 16:00
        (225088590, 90, "2026-03-01 08:00:00", 111, 1, "Teq .125 P1", 1, 1, None, "Tequendama", "teq"),
        (225088590, 91, "2026-03-01 16:00:00", 111, 2, "Teq .125 P2", 1, 1, None, "Tequendama", "teq"),
    ]
    conn.executemany(
        "INSERT INTO events (device_id, idx, timestamp, card, door, door_name, "
        "granted, reason, direction, sede, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


class TestKpis(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def test_fetch_excludes_noise(self):
        rows = kpis.fetch_rows(self.conn, {})
        # 4 Palmetto validos (idx 1-4) + 2 Teq = 6; excluye reason20 (idx5) y granted0 (idx6)
        self.assertEqual(len(rows), 6)

    def test_arrival_departure_palmetto_uses_direction(self):
        rows = kpis.fetch_rows(self.conn, {})
        ad = kpis.arrival_departure(rows)
        # Palmetto: llegadas 07:00 y 09:00 -> prom 08:00 ; salidas 18:00 y 17:00 -> prom 17:30
        self.assertEqual(ad["Palmetto"]["arrival"], "08:00")
        self.assertEqual(ad["Palmetto"]["departure"], "17:30")

    def test_arrival_departure_teq_uses_first_last(self):
        rows = kpis.fetch_rows(self.conn, {})
        ad = kpis.arrival_departure(rows)
        # Teq card111: primer swipe 08:00 = llegada, ultimo 16:00 = salida
        self.assertEqual(ad["Tequendama"]["arrival"], "08:00")
        self.assertEqual(ad["Tequendama"]["departure"], "16:00")

    def test_late_arrivals_threshold(self):
        rows = kpis.fetch_rows(self.conn, {})
        late = kpis.late_arrivals(rows, "08:00")
        # Palmetto: 2 llegadas (07:00, 09:00); >08:00 = 1 (la de 09:00)
        self.assertEqual(late["Palmetto"]["late"], 1)
        self.assertEqual(late["Palmetto"]["total"], 2)

    def test_attendance_unique_cards(self):
        rows = kpis.fetch_rows(self.conn, {})
        att = kpis.attendance(rows)
        # 2026-03-01: Palmetto tarjetas unicas {111,222}=2 ; Teq {111}=1
        self.assertEqual(att["latest"]["Palmetto"], 2)
        self.assertEqual(att["latest"]["Tequendama"], 1)

    def test_hourly_has_24_buckets(self):
        rows = kpis.fetch_rows(self.conn, {})
        h = kpis.hourly(rows)
        self.assertEqual(len(h), 24)
        self.assertEqual(h[7]["count"], 1)  # un evento a las 07:xx (Palmetto idx1)

    def test_top_doors_desc(self):
        rows = kpis.fetch_rows(self.conn, {})
        td = kpis.top_doors(rows)
        self.assertEqual(td[0]["door"], "(P) Porteria")  # 4 eventos, el mas usado

    def test_compute_kpis_shape(self):
        k = kpis.compute_kpis(self.conn, {}, "08:00")
        for key in ("range", "arrival_departure", "attendance", "late",
                    "hourly", "daily_volume", "top_doors", "top_cards"):
            self.assertIn(key, k)


if __name__ == "__main__":
    unittest.main()

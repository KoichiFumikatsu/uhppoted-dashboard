import unittest
from doors_analytics import portal_auth as pa


class TestCrypto(unittest.TestCase):
    def test_password_roundtrip(self):
        salt, h = pa.hash_password("secreta123")
        self.assertTrue(pa.verify_password("secreta123", salt, h))
        self.assertFalse(pa.verify_password("otra", salt, h))

    def test_password_salt_differs(self):
        s1, h1 = pa.hash_password("x")
        s2, h2 = pa.hash_password("x")
        self.assertNotEqual(s1, s2)      # salt aleatorio
        self.assertNotEqual(h1, h2)


class TestToken(unittest.TestCase):
    SECRET = b"0123456789abcdef0123456789abcdef"

    def test_token_roundtrip(self):
        t = pa.make_token("koichi", self.SECRET, now_ts=1000, ttl=100)
        self.assertEqual(pa.verify_token(t, self.SECRET, now_ts=1050), "koichi")

    def test_token_expired(self):
        t = pa.make_token("koichi", self.SECRET, now_ts=1000, ttl=100)
        self.assertIsNone(pa.verify_token(t, self.SECRET, now_ts=1200))  # vencido

    def test_token_tampered(self):
        t = pa.make_token("koichi", self.SECRET, now_ts=1000, ttl=100)
        self.assertIsNone(pa.verify_token(t + "x", self.SECRET, now_ts=1050))
        self.assertIsNone(pa.verify_token(t, b"otro-secreto-distinto-de-32bytes!", now_ts=1050))

    def test_token_wrong_user_not_forgeable(self):
        # cambiar el payload sin la firma correcta no valida
        import base64
        forged = base64.urlsafe_b64encode(b"admin|9999999999").decode() + ".deadbeef"
        self.assertIsNone(pa.verify_token(forged, self.SECRET, now_ts=1050))


class TestCaps(unittest.TestCase):
    def test_star_grants_all(self):
        self.assertTrue(pa.user_has_cap(["*"], "editar_tarjetas"))
    def test_specific_cap(self):
        self.assertTrue(pa.user_has_cap(["ver_eventos"], "ver_eventos"))
        self.assertFalse(pa.user_has_cap(["ver_eventos"], "editar_tarjetas"))
    def test_sesion_only_needs_login(self):
        self.assertTrue(pa.user_has_cap(["ver_eventos"], "sesion"))
        self.assertTrue(pa.user_has_cap([], "sesion"))

    def test_required_cap_mapping(self):
        self.assertEqual(pa.required_cap("/analytics/api/kpis?from=x"), "ver_dashboard")
        self.assertEqual(pa.required_cap("/analytics/api/events?page=1"), "ver_eventos")
        self.assertEqual(pa.required_cap("/analytics/dashboard.html"), "ver_dashboard")
        self.assertEqual(pa.required_cap("/analytics/"), "ver_eventos")
        self.assertEqual(pa.required_cap("/schedules/api/profile/7"), "editar_horarios")
        self.assertEqual(pa.required_cap("/schedules/api/card/123"), "editar_tarjetas")
        self.assertEqual(pa.required_cap("/schedules/api/publish"), "publicar_acl")
        self.assertEqual(pa.required_cap("/schedules/api/controllers-refresh"), "gestionar_controladores")
        self.assertEqual(pa.required_cap("/door-opener/open-door"), "abrir_puerta")
        self.assertEqual(pa.required_cap("/schedules/"), "sesion")


class TestUsers(unittest.TestCase):
    def _base(self):
        return pa.create_user({}, "koichi", "Koichi", "clave", ["*"])

    def test_create_and_verify(self):
        u = self._base()
        self.assertIn("koichi", u)
        self.assertNotIn("password", u["koichi"])          # nunca texto plano
        self.assertTrue(pa.verify_password("clave", u["koichi"]["salt"], u["koichi"]["hash"]))
        self.assertEqual(u["koichi"]["caps"], ["*"])

    def test_create_duplicate_raises(self):
        u = self._base()
        with self.assertRaises(ValueError):
            pa.create_user(u, "koichi", "x", "y", [])

    def test_update_caps_and_password(self):
        u = self._base()
        pa.create_user(u, "gisella", "Gisella", "g1", ["ver_eventos"])
        pa.update_user(u, "gisella", caps=["ver_eventos", "ver_dashboard"])
        self.assertEqual(set(u["gisella"]["caps"]), {"ver_eventos", "ver_dashboard"})
        pa.set_password(u, "gisella", "nueva")
        self.assertTrue(pa.verify_password("nueva", u["gisella"]["salt"], u["gisella"]["hash"]))

    def test_delete(self):
        u = self._base()
        pa.create_user(u, "tmp", "T", "x", [])
        pa.delete_user(u, "tmp")
        self.assertNotIn("tmp", u)

    def test_public_users_hides_secrets(self):
        u = self._base()
        pub = pa.public_users(u)
        self.assertEqual(pub[0]["username"], "koichi")
        self.assertNotIn("hash", pub[0])
        self.assertNotIn("salt", pub[0])


if __name__ == "__main__":
    unittest.main()

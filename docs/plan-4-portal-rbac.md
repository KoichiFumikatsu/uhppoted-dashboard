# Portal RBAC "AZC Accesos" (uhppoted dashboard) — Plan 4

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un sistema de login por usuario con permisos granulares por capacidad, que gobierna TODAS las piezas (analytics, schedules/horarios/tarjetas/publish, door-opener) con una sola identidad. Reemplaza el basic-auth compartido y cierra `/schedules/` y `/door-opener/` (hoy sin auth). Portal único donde cada persona ve solo lo que su permiso habilita.

**Architecture:** Servicio de auth `doors-auth` (stdlib http.server, `127.0.0.1:8448`) con usuarios en `/etc/uhppoted/portal/users.json` y sesión por cookie firmada HMAC. La lógica pura (hash de password PBKDF2, firmar/verificar token, mapa URI→capacidad, CRUD de usuarios) vive en `doors_analytics/portal_auth.py` (TDD). nginx usa `auth_request` a `/_auth` para gatear cada área; el servicio decide la capacidad requerida desde `X-Original-URI`. UI estática de portal (login, landing por capacidades, gestión de usuarios). El panel nativo uhppoted (`/`) conserva su login propio (super-admin).

**Tech Stack:** Python 3.8 stdlib (`hashlib.pbkdf2_hmac`, `hmac`, `secrets`, `base64`, `http.server`, `json`, `unittest`), nginx, systemd, HTML/JS vanilla.

## Global Constraints

- **Solo stdlib de Python 3.8.** NO pip/pytest/deps. Tests con `python3 -m unittest`. Autorar local + `scp`; tests/git/systemd por SSH en `192.168.12.25`.
- **No modificar el control-plane funcional** (schedule-manager, uhppoted-httpd, door-opener, doors-analytics-api). Solo se AGREGA gating por delante (nginx) y un servicio nuevo.
- **Servicio nuevo:** `doors-auth` = `/usr/local/bin/doors-auth`, `127.0.0.1:8448`, HTTP plano, systemd `Type=simple Restart=on-failure`, corre como root.
- **Estado:** `/etc/uhppoted/portal/users.json` (usuarios, hashes) y `/etc/uhppoted/portal/secret` (32 bytes aleatorios). Ambos `chmod 600 root:root`.
- **Seguridad de la cookie:** `HttpOnly; Secure; SameSite=Lax; Path=/`. Token = base64(`username|expiry`)+"."+HMAC-SHA256 hex (secret del archivo). Verificación con `hmac.compare_digest` y chequeo de expiración. TTL 12h.
- **Password:** `pbkdf2_hmac('sha256', pw, salt, 200000)`; guardar `salt` hex + hash hex. NUNCA texto plano. `verify` con `compare_digest`.
- **Capacidades** (claves): `ver_eventos, ver_dashboard, editar_tarjetas, editar_horarios, publicar_acl, gestionar_controladores, abrir_puerta, gestionar_usuarios`. La capacidad especial `*` = super-admin (pasa cualquier chequeo).
- **Mapa URI→capacidad** (autoridad única en `required_cap`): kpis→`ver_dashboard`, analytics events/otros→`ver_eventos`, schedules profile→`editar_horarios`, schedules card/bulk-assign/doors→`editar_tarjetas`, schedules publish/generate-tsv/role-profiles→`publicar_acl`, schedules controllers→`gestionar_controladores`, schedules teq-events→`ver_eventos`, door-opener→`abrir_puerta`; default→`sesion` (cualquier login válido).
- **Páginas públicas (sin auth_request):** el propio servicio `/auth/*` (se autovalida) y los estáticos de `/portal/*` (shells sin datos; los datos se piden a `/auth/*` que valida). El login DEBE ser accesible sin sesión.
- **NO tocar `location /`** (panel nativo uhppoted): conserva su login propio. El portal solo muestra su link a super-admin.

**Contrato de sesión/capacidades** (lo que la UI consume de `GET /auth/me`): `{"username": "...", "name": "...", "caps": ["ver_eventos", ...]}`.

---

## File Structure

```
/root/uhppoted-dashboard/
  doors_analytics/portal_auth.py     # crypto + token + required_cap + users CRUD (puro, TDD)
  tests/test_portal_auth.py
  service/doors-auth                 # servicio HTTP :8448
  ui/portal-login.html  ui/portal-index.html  ui/portal-users.html
/usr/local/bin/doors-auth
/etc/systemd/system/doors-auth.service
/etc/uhppoted/portal/{users.json,secret}
/home/azcweb/web/doors.azc.com.co/public_html/portal/{login.html,index.html,users.html}
/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf   # auth_request gating
```

---

### Task 1: Crypto + sesión + mapa de capacidades (`portal_auth.py`)

**Files:**
- Create: `/root/uhppoted-dashboard/doors_analytics/portal_auth.py`
- Test: `/root/uhppoted-dashboard/tests/test_portal_auth.py`

**Interfaces:**
- Produces (puras): `hash_password(pw, salt=None) -> (salt_hex, hash_hex)`; `verify_password(pw, salt_hex, hash_hex) -> bool`; `make_token(username, secret, now_ts, ttl=43200) -> str`; `verify_token(token, secret, now_ts) -> username|None`; `user_has_cap(caps, required) -> bool` (`"*"` o pertenencia; `required=="sesion"` siempre True si hay caps/lista); `required_cap(uri) -> str`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_portal_auth.py
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_portal_auth -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'doors_analytics.portal_auth'`.

- [ ] **Step 3: Implementar la parte de crypto/sesión/caps**

```python
# doors_analytics/portal_auth.py
import base64
import hashlib
import hmac
import json
import os
import secrets

_ITER = 200000


def hash_password(pw, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), _ITER)
    return salt, h.hex()


def verify_password(pw, salt_hex, hash_hex):
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), _ITER)
    return hmac.compare_digest(h.hex(), hash_hex)


def make_token(username, secret, now_ts, ttl=43200):
    payload = "%s|%d" % (username, int(now_ts) + int(ttl))
    b = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret, b.encode(), hashlib.sha256).hexdigest()
    return b + "." + sig


def verify_token(token, secret, now_ts):
    try:
        b, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expect = hmac.new(secret, b.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        payload = base64.urlsafe_b64decode(b.encode()).decode()
        username, exp = payload.rsplit("|", 1)
    except Exception:
        return None
    if int(now_ts) >= int(exp):
        return None
    return username


def user_has_cap(caps, required):
    if required == "sesion":
        return True
    return "*" in caps or required in caps


_RULES = [
    ("/analytics/api/kpis", "ver_dashboard"),
    ("/analytics/api/events", "ver_eventos"),
    ("/analytics/dashboard", "ver_dashboard"),
    ("/analytics/", "ver_eventos"),
    ("/schedules/api/profile", "editar_horarios"),
    ("/schedules/api/card", "editar_tarjetas"),
    ("/schedules/api/bulk-assign", "editar_tarjetas"),
    ("/schedules/api/doors", "editar_tarjetas"),
    ("/schedules/api/publish", "publicar_acl"),
    ("/schedules/api/generate-tsv", "publicar_acl"),
    ("/schedules/api/role-profiles", "publicar_acl"),
    ("/schedules/api/controllers", "gestionar_controladores"),
    ("/schedules/api/teq-events", "ver_eventos"),
    ("/door-opener/", "abrir_puerta"),
]


def required_cap(uri):
    u = uri.split("?", 1)[0]
    for prefix, cap in _RULES:
        if u.startswith(prefix):
            return cap
    return "sesion"
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_portal_auth -v`
Expected: PASS (los tests de crypto/token/caps; el test de users llega en Task 2).

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: portal_auth crypto + session token + required_cap"
```

---

### Task 2: Store de usuarios (CRUD puro) en `portal_auth.py`

**Files:**
- Modify: `/root/uhppoted-dashboard/doors_analytics/portal_auth.py`
- Test: `/root/uhppoted-dashboard/tests/test_portal_auth.py`

**Interfaces:**
- Produces: `load_users(path)->dict`; `save_users(path, users)`; `create_user(users, username, name, password, caps)->users` (falla si existe); `update_user(users, username, name=None, caps=None)`; `set_password(users, username, password)`; `delete_user(users, username)`; `public_users(users)->list` (sin salt/hash). `CAPS` = lista de (clave, etiqueta) para la UI.

- [ ] **Step 1: Escribir el test que falla (agregar a test_portal_auth.py)**

```python
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
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_portal_auth -v`
Expected: FAIL (`AttributeError: ... 'create_user'`).

- [ ] **Step 3: Implementar el store (agregar a portal_auth.py)**

```python
CAPS = [
    ("ver_eventos", "Ver Eventos"),
    ("ver_dashboard", "Ver Dashboard"),
    ("editar_tarjetas", "Editar Tarjetas"),
    ("editar_horarios", "Editar Horarios"),
    ("publicar_acl", "Publicar ACL a controladores"),
    ("gestionar_controladores", "Gestionar Controladores"),
    ("abrir_puerta", "Abrir Puerta"),
    ("gestionar_usuarios", "Gestionar Usuarios"),
]
_VALID_CAPS = {k for k, _ in CAPS} | {"*"}


def load_users(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_users(path, users):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f, indent=1)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _clean_caps(caps):
    return [c for c in (caps or []) if c in _VALID_CAPS]


def create_user(users, username, name, password, caps):
    if username in users:
        raise ValueError("usuario ya existe")
    salt, h = hash_password(password)
    users[username] = {"name": name, "salt": salt, "hash": h, "caps": _clean_caps(caps)}
    return users


def update_user(users, username, name=None, caps=None):
    if username not in users:
        raise ValueError("no existe")
    if name is not None:
        users[username]["name"] = name
    if caps is not None:
        users[username]["caps"] = _clean_caps(caps)
    return users


def set_password(users, username, password):
    if username not in users:
        raise ValueError("no existe")
    salt, h = hash_password(password)
    users[username]["salt"] = salt
    users[username]["hash"] = h
    return users


def delete_user(users, username):
    users.pop(username, None)
    return users


def public_users(users):
    return [{"username": u, "name": d.get("name", u), "caps": d.get("caps", [])}
            for u, d in sorted(users.items())]
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd /root/uhppoted-dashboard && python3 -m unittest tests.test_portal_auth -v`
Expected: PASS (todos). Full suite: `python3 -m unittest discover -s tests` → OK (29 previos + los nuevos).

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: portal_auth users CRUD store"
```

---

### Task 3: Servicio `doors-auth` (:8448) + bootstrap super-admin + systemd

**Files:**
- Create: `/root/uhppoted-dashboard/service/doors-auth`, deploy a `/usr/local/bin/`
- Create: `/etc/systemd/system/doors-auth.service`
- Create: `/etc/uhppoted/portal/{secret,users.json}` (bootstrap)

**Interfaces:**
- HTTP `127.0.0.1:8448`:
  - `POST /auth/login` `{username,password}` → 200 `{name,caps}` + Set-Cookie `doors_session` | 401.
  - `POST /auth/logout` → 200 + cookie vacía.
  - `GET /auth/me` → `{username,name,caps}` | 401.
  - `GET /auth/check` (target de auth_request; lee `Cookie` + header `X-Original-URI`) → 200 | 401 (sin sesión) | 403 (sin capacidad). Sin body.
  - `GET /auth/caps` → `[[clave,etiqueta],...]`.
  - `GET /auth/users` · `POST /auth/users` · `PUT /auth/users/<u>` · `POST /auth/users/<u>/password` · `DELETE /auth/users/<u>` → requieren `gestionar_usuarios`.

- [ ] **Step 1: Escribir el servicio**

```python
# service/doors-auth
#!/usr/bin/env python3
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

sys.path.insert(0, "/root/uhppoted-dashboard")
from doors_analytics import portal_auth as pa

USERS_PATH = "/etc/uhppoted/portal/users.json"
SECRET_PATH = "/etc/uhppoted/portal/secret"
COOKIE = "doors_session"


def _secret():
    with open(SECRET_PATH, "rb") as f:
        return f.read()


def _current_user(handler):
    # devuelve (username, caps) o (None, None)
    cookie = handler.headers.get("Cookie", "")
    tok = None
    for part in cookie.split(";"):
        p = part.strip()
        if p.startswith(COOKIE + "="):
            tok = p[len(COOKIE) + 1:]
    if not tok:
        return None, None
    username = pa.verify_token(tok, _secret(), now_ts=time.time())
    if not username:
        return None, None
    users = pa.load_users(USERS_PATH)
    if username not in users:
        return None, None
    return username, users[username].get("caps", [])


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj=None, cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if obj is not None:
            self.wfile.write(json.dumps(obj).encode())

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def _require(self, cap):
        user, caps = _current_user(self)
        if user is None:
            self._send(401, {"error": "no session"}); return None
        if not pa.user_has_cap(caps, cap):
            self._send(403, {"error": "forbidden"}); return None
        return user

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/auth/check":
            user, caps = _current_user(self)
            if user is None:
                self._send(401); return
            cap = pa.required_cap(self.headers.get("X-Original-URI", "/"))
            self._send(200 if pa.user_has_cap(caps, cap) else 403); return
        if path == "/auth/me":
            user, caps = _current_user(self)
            if user is None:
                self._send(401, {"error": "no session"}); return
            users = pa.load_users(USERS_PATH)
            self._send(200, {"username": user, "name": users[user].get("name", user), "caps": caps}); return
        if path == "/auth/caps":
            self._send(200, pa.CAPS); return
        if path == "/auth/users":
            if self._require("gestionar_usuarios") is None: return
            self._send(200, pa.public_users(pa.load_users(USERS_PATH))); return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/auth/login":
            b = self._body()
            users = pa.load_users(USERS_PATH)
            u = users.get(b.get("username", ""))
            if u and pa.verify_password(b.get("password", ""), u["salt"], u["hash"]):
                tok = pa.make_token(b["username"], _secret(), now_ts=time.time())
                c = "%s=%s; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=43200" % (COOKIE, tok)
                self._send(200, {"name": u.get("name"), "caps": u.get("caps", [])}, cookie=c)
            else:
                self._send(401, {"error": "credenciales invalidas"})
            return
        if path == "/auth/logout":
            self._send(200, {"ok": True}, cookie="%s=; Max-Age=0; Path=/" % COOKIE); return
        if path == "/auth/users":
            if self._require("gestionar_usuarios") is None: return
            b = self._body(); users = pa.load_users(USERS_PATH)
            try:
                pa.create_user(users, b["username"], b.get("name", b["username"]), b["password"], b.get("caps", []))
            except (ValueError, KeyError) as e:
                self._send(400, {"error": str(e)}); return
            pa.save_users(USERS_PATH, users); self._send(200, {"ok": True}); return
        if path.startswith("/auth/users/") and path.endswith("/password"):
            if self._require("gestionar_usuarios") is None: return
            uname = path[len("/auth/users/"):-len("/password")]
            b = self._body(); users = pa.load_users(USERS_PATH)
            try:
                pa.set_password(users, uname, b["password"])
            except (ValueError, KeyError) as e:
                self._send(400, {"error": str(e)}); return
            pa.save_users(USERS_PATH, users); self._send(200, {"ok": True}); return
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/auth/users/"):
            if self._require("gestionar_usuarios") is None: return
            uname = path[len("/auth/users/"):]
            b = self._body(); users = pa.load_users(USERS_PATH)
            try:
                pa.update_user(users, uname, name=b.get("name"), caps=b.get("caps"))
            except ValueError as e:
                self._send(400, {"error": str(e)}); return
            pa.save_users(USERS_PATH, users); self._send(200, {"ok": True}); return
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/auth/users/"):
            if self._require("gestionar_usuarios") is None: return
            uname = path[len("/auth/users/"):]
            users = pa.load_users(USERS_PATH); pa.delete_user(users, uname)
            pa.save_users(USERS_PATH, users); self._send(200, {"ok": True}); return
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8448), H).serve_forever()
```

- [ ] **Step 2: Bootstrap (secret + super-admin) y deploy**

```bash
mkdir -p /etc/uhppoted/portal
python3 -c "import secrets;open('/etc/uhppoted/portal/secret','wb').write(secrets.token_bytes(32))"
chmod 600 /etc/uhppoted/portal/secret
# crear super-admin koichi (cap '*') con password temporal — CAMBIAR luego desde la UI
python3 - <<'PY'
import sys; sys.path.insert(0,"/root/uhppoted-dashboard")
from doors_analytics import portal_auth as pa
users = pa.load_users("/etc/uhppoted/portal/users.json")
if "koichi" not in users:
    pa.create_user(users, "koichi", "Koichi (super-admin)", "<contrasena-bootstrap-temporal>", ["*"])
    pa.save_users("/etc/uhppoted/portal/users.json", users)
    print("super-admin koichi creado")
else:
    print("koichi ya existe")
PY
chmod 600 /etc/uhppoted/portal/users.json
cp /root/uhppoted-dashboard/service/doors-auth /usr/local/bin/doors-auth
chmod +x /usr/local/bin/doors-auth
```

```ini
# /etc/systemd/system/doors-auth.service
[Unit]
Description=uhppoted portal - auth/RBAC (127.0.0.1:8448)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/doors-auth
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Arrancar y verificar (login → me → check)**

```bash
systemctl daemon-reload && systemctl enable --now doors-auth.service
systemctl is-active doors-auth.service
# login guarda la cookie
curl -s -c /tmp/ck.txt -X POST http://127.0.0.1:8448/auth/login -d '{"username":"koichi","password":"<contrasena-bootstrap-temporal>"}' -w "\n[login %{http_code}]\n"
# me
curl -s -b /tmp/ck.txt http://127.0.0.1:8448/auth/me -w "\n[me %{http_code}]\n"
# check con capacidad que SÍ tiene (super-admin *): 200
curl -s -b /tmp/ck.txt -o /dev/null -w "check editar_tarjetas (admin) -> %{http_code}\n" -H "X-Original-URI: /schedules/api/card/1" http://127.0.0.1:8448/auth/check
# check sin cookie: 401
curl -s -o /dev/null -w "check sin cookie -> %{http_code}\n" -H "X-Original-URI: /analytics/" http://127.0.0.1:8448/auth/check
```
Expected: `is-active` active; login 200 con Set-Cookie; me 200 con caps `["*"]`; check con cookie 200; sin cookie 401.

- [ ] **Step 4: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: doors-auth service (:8448) + bootstrap super-admin + systemd"
```

---

### Task 4: Gating en nginx (auth_request) — reemplaza basic-auth, cierra schedules/door-opener

**Files:**
- Modify: `/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf`

⚠️ Producción. **Backup + `nginx -t` + rollback** obligatorio. Si `nginx -t` falla, restaurar y parar (BLOCKED).

**Interfaces:**
- Consumes: `doors-auth` (:8448). Produces: cada área gateada por sesión+capacidad; login redirect.

- [ ] **Step 1: Backup**

```bash
cp /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf \
   /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf.bak.rbac-$(date +%Y%m%d-%H%M%S)
```

- [ ] **Step 2: Aplicar los cambios con un script Python (idempotente)**

```bash
python3 - <<'PY'
p = "/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf"
s = open(p).read()
assert "location /_auth" not in s, "ya aplicado"

# 1) quitar el basic-auth de /analytics (2 bloques)
s = s.replace('        auth_basic "AZC Accesos - Restringido";\n', '')
s = s.replace('        auth_basic_user_file /etc/nginx/doors-analytics.htpasswd;\n', '')

# 2) bloques nuevos: servicio auth (abierto), portal estatico (abierto), _auth interno
anchor = "    location /analytics/api/ {"
newblocks = (
    "    location /auth/ {\n"
    "        proxy_pass http://127.0.0.1:8448/auth/;\n"
    "        proxy_set_header Host $host;\n"
    "    }\n"
    "    location /portal/ {\n"
    "        alias /home/azcweb/web/doors.azc.com.co/public_html/portal/;\n"
    "        index index.html;\n"
    "    }\n"
    "    location = /_auth {\n"
    "        internal;\n"
    "        proxy_pass http://127.0.0.1:8448/auth/check;\n"
    "        proxy_pass_request_body off;\n"
    "        proxy_set_header Content-Length \"\";\n"
    "        proxy_set_header X-Original-URI $request_uri;\n"
    "    }\n"
)
s = s.replace(anchor, newblocks + anchor, 1)

# 3) inyectar auth_request en cada area protegida.
#    En paginas estaticas: 401 redirige al login. En APIs: pasa el codigo.
API = "        auth_request /_auth;\n"
PAGE = "        auth_request /_auth;\n        error_page 401 =302 /portal/login.html;\n"
# analytics/api/  (API) -> auth_request
s = s.replace("    location /analytics/api/ {\n", "    location /analytics/api/ {\n" + API, 1)
# analytics/ (pagina) -> auth_request + redirect login
s = s.replace("    location /analytics/ {\n", "    location /analytics/ {\n" + PAGE, 1)
# schedules/api/ (API)
s = s.replace("    location /schedules/api/ {\n", "    location /schedules/api/ {\n" + API, 1)
# schedules/ (pagina)
s = s.replace("    location /schedules/ {\n", "    location /schedules/ {\n" + PAGE, 1)
# door-opener/ (lo llama el panel via fetch -> tratar como API)
s = s.replace("    location /door-opener/ {\n", "    location /door-opener/ {\n" + API, 1)

open(p, "w").write(s)
print("gating aplicado")
PY
```

- [ ] **Step 3: Validar y recargar (rollback si falla)**

```bash
if nginx -t 2>&1 | grep -q successful; then systemctl reload nginx; echo "NGINX OK"; \
else echo "FALLO -> ROLLBACK"; cp $(ls -t /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf.bak.rbac-* | head -1) /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf; nginx -t && systemctl reload nginx; exit 1; fi
```

- [ ] **Step 4: Verificar gating (sin sesión = bloqueado; con sesión admin = pasa)**

```bash
# sin cookie: analytics API 401, schedules API 401, door-opener 401, pagina analytics 302 al login
curl -sk -o /dev/null -w "analytics/api sin sesion -> %{http_code}\n" "https://doors.azc.com.co/analytics/api/events?page_size=1"
curl -sk -o /dev/null -w "schedules/api sin sesion -> %{http_code}\n" "https://doors.azc.com.co/schedules/api/profiles"
curl -sk -o /dev/null -w "door-opener sin sesion -> %{http_code}\n" "https://doors.azc.com.co/door-opener/"
curl -sk -o /dev/null -w "pagina analytics sin sesion -> %{http_code} (302=login)\n" "https://doors.azc.com.co/analytics/"
# login por el dominio y probar con cookie
curl -sk -c /tmp/ck.txt -X POST https://doors.azc.com.co/auth/login -d '{"username":"koichi","password":"<contrasena-bootstrap-temporal>"}' -o /dev/null -w "login -> %{http_code}\n"
curl -sk -b /tmp/ck.txt -o /dev/null -w "analytics/api CON sesion admin -> %{http_code}\n" "https://doors.azc.com.co/analytics/api/events?page_size=1"
curl -sk -b /tmp/ck.txt -o /dev/null -w "schedules/api CON sesion admin -> %{http_code}\n" "https://doors.azc.com.co/schedules/api/profiles"
```
Expected: sin sesión → 401 (APIs) y 302 (página analytics); login 200; con sesión admin → 200 en ambas APIs. **El basic-auth viejo ya no aplica** (la credencial htpasswd deja de usarse).

- [ ] **Step 5: Commit (snapshot del vhost al repo)**

```bash
cp /home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf /root/uhppoted-dashboard/deploy/nginx.ssl.conf.snapshot
cd /root/uhppoted-dashboard && git add deploy/ && git commit -m "ops: nginx auth_request RBAC gating (reemplaza basic-auth; cierra schedules + door-opener)"
```

---

### Task 5: Portal UI (login, landing por capacidades, gestión de usuarios) + usuarios reales

**Files:**
- Create: `ui/portal-login.html`, `ui/portal-index.html`, `ui/portal-users.html` en el repo; deploy a `public_html/portal/{login,index,users}.html` (owner azcweb).

**Interfaces:**
- Consumes: `/auth/login`, `/auth/me`, `/auth/logout`, `/auth/caps`, `/auth/users*`.
- Produces: `https://doors.azc.com.co/portal/` (landing por capacidades), `/portal/login.html`, `/portal/users.html`.

- [ ] **Step 1: Crear las 3 páginas**

`ui/portal-login.html`:
```html
<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>AZC Accesos — Ingreso</title>
<style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0f1720;color:#e6edf3;font-family:system-ui,Segoe UI,sans-serif}
.box{background:#182430;border:1px solid #2a3a49;border-radius:12px;padding:28px;width:300px}
h1{font-size:18px;margin:0 0 18px}
input{width:100%;box-sizing:border-box;background:#0f1720;border:1px solid #2a3a49;color:#e6edf3;padding:10px;border-radius:6px;margin-bottom:12px}
button{width:100%;background:#3987e5;color:#04121f;border:none;padding:10px;border-radius:6px;font-weight:700;cursor:pointer}
.err{color:#ff7b72;font-size:13px;min-height:18px;margin-top:8px}
</style></head><body>
<form class="box" id="f">
  <h1>AZC Accesos</h1>
  <input id="u" placeholder="Usuario" autocomplete="username">
  <input id="p" type="password" placeholder="Contraseña" autocomplete="current-password">
  <button>Ingresar</button>
  <div class="err" id="e"></div>
</form>
<script>
document.getElementById("f").onsubmit=async(ev)=>{
  ev.preventDefault();
  const r=await fetch("/auth/login",{method:"POST",body:JSON.stringify({username:u.value.trim(),password:p.value})});
  if(r.ok){location.href="/portal/";}else{document.getElementById("e").textContent="Usuario o contraseña incorrectos";}
};
</script></body></html>
```

`ui/portal-index.html`:
```html
<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>AZC Accesos</title>
<style>
body{margin:0;background:#0f1720;color:#e6edf3;font-family:system-ui,Segoe UI,sans-serif}
header{padding:16px 22px;border-bottom:1px solid #2a3a49;display:flex;justify-content:space-between;align-items:center}
header b{font-size:18px}.who{color:#9fb0c0;font-size:13px}
a.logout{color:#9fb0c0;text-decoration:none;font-size:13px;margin-left:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;padding:22px}
.tile{display:block;background:#182430;border:1px solid #2a3a49;border-radius:10px;padding:20px;color:#e6edf3;text-decoration:none}
.tile:hover{border-color:#3987e5}.tile h3{margin:0 0 6px;font-size:15px}.tile p{margin:0;color:#9fb0c0;font-size:12px}
</style></head><body>
<header><b>AZC Accesos</b><span><span class="who" id="who"></span><a class="logout" href="#" id="lo">Salir</a></span></header>
<div class="grid" id="grid"></div>
<script>
const TILES=[
 {cap:"ver_eventos",href:"/analytics/",t:"Eventos",d:"Historial de accesos por sede"},
 {cap:"ver_dashboard",href:"/analytics/dashboard.html",t:"Dashboard",d:"KPIs de acceso"},
 {cap:"editar_horarios",href:"/schedules/",t:"Horarios",d:"Time profiles"},
 {cap:"editar_tarjetas",href:"/schedules/",t:"Tarjetas",d:"Altas/bajas y grupos"},
 {cap:"publicar_acl",href:"/schedules/",t:"Publicar",d:"Empujar a controladores"},
 {cap:"gestionar_controladores",href:"/schedules/",t:"Controladores",d:"Estado y gestión"},
 {cap:"gestionar_usuarios",href:"/portal/users.html",t:"Usuarios",d:"Personas y permisos"},
 {cap:"*",href:"/",t:"Panel nativo",d:"Panel uhppoted (super-admin)"},
];
(async()=>{
 const r=await fetch("/auth/me"); if(!r.ok){location.href="/portal/login.html";return;}
 const me=await r.json();
 document.getElementById("who").textContent=me.name+" ("+me.username+")";
 const has=c=>me.caps.includes("*")||me.caps.includes(c);
 const g=document.getElementById("grid");
 const seen=new Set();
 for(const t of TILES){ if(!has(t.cap)) continue; if(seen.has(t.href+t.t))continue; seen.add(t.href+t.t);
   const a=document.createElement("a");a.className="tile";a.href=t.href;
   const h=document.createElement("h3");h.textContent=t.t;const p=document.createElement("p");p.textContent=t.d;
   a.appendChild(h);a.appendChild(p);g.appendChild(a);
 }
})();
document.getElementById("lo").onclick=async(e)=>{e.preventDefault();await fetch("/auth/logout",{method:"POST"});location.href="/portal/login.html";};
</script></body></html>
```

`ui/portal-users.html`:
```html
<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Usuarios — AZC Accesos</title>
<style>
body{margin:0;background:#0f1720;color:#e6edf3;font-family:system-ui,Segoe UI,sans-serif}
header{padding:16px 22px;border-bottom:1px solid #2a3a49}header a{color:#9fb0c0;text-decoration:none;font-size:13px}
.wrap{padding:22px;max-width:760px}
.card{background:#182430;border:1px solid #2a3a49;border-radius:10px;padding:16px;margin-bottom:16px}
input{background:#0f1720;border:1px solid #2a3a49;color:#e6edf3;padding:8px;border-radius:6px;margin:4px 6px 4px 0}
button{background:#3987e5;color:#04121f;border:none;padding:8px 12px;border-radius:6px;font-weight:700;cursor:pointer}
button.sec{background:#182430;color:#e6edf3;border:1px solid #2a3a49}
label.cap{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:#9fb0c0;margin:3px 10px 3px 0}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #2a3a49}
</style></head><body>
<header><b>Usuarios</b> &nbsp; <a href="/portal/">‹ Volver</a></header>
<div class="wrap">
 <div class="card"><h3>Nuevo usuario</h3>
  <input id="nu" placeholder="usuario"><input id="nn" placeholder="Nombre"><input id="np" type="password" placeholder="contraseña"><br>
  <div id="ncaps"></div>
  <button id="create">Crear</button>
 </div>
 <div class="card"><h3>Existentes</h3><table id="tbl"><thead><tr><th>Usuario</th><th>Nombre</th><th>Capacidades</th><th></th></tr></thead><tbody></tbody></table></div>
</div>
<script>
let CAPS=[];
function capsChecklist(container,selected){container.innerHTML="";CAPS.forEach(([k,label])=>{
  const l=document.createElement("label");l.className="cap";
  const c=document.createElement("input");c.type="checkbox";c.value=k;c.checked=selected.includes(k)||selected.includes("*");
  l.appendChild(c);l.appendChild(document.createTextNode(label));container.appendChild(l);});}
function readChecklist(container){return [...container.querySelectorAll("input:checked")].map(c=>c.value);}
async function load(){
  const rc=await fetch("/auth/caps"); CAPS=await rc.json(); capsChecklist(document.getElementById("ncaps"),[]);
  const r=await fetch("/auth/users"); if(!r.ok){location.href="/portal/login.html";return;}
  const users=await r.json(); const tb=document.querySelector("#tbl tbody"); tb.innerHTML="";
  users.forEach(u=>{const tr=document.createElement("tr");
    [u.username,u.name,(u.caps.includes("*")?"TODO":u.caps.join(", "))].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.appendChild(td)});
    const td=document.createElement("td");
    const del=document.createElement("button");del.className="sec";del.textContent="Borrar";
    del.onclick=async()=>{if(confirm("Borrar "+u.username+"?")){await fetch("/auth/users/"+u.username,{method:"DELETE"});load();}};
    if(!u.caps.includes("*")) td.appendChild(del);
    tr.appendChild(td);tb.appendChild(tr);});
}
document.getElementById("create").onclick=async()=>{
  const body={username:nu.value.trim(),name:nn.value.trim(),password:np.value,caps:readChecklist(document.getElementById("ncaps"))};
  const r=await fetch("/auth/users",{method:"POST",body:JSON.stringify(body)});
  if(r.ok){nu.value=nn.value=np.value="";load();}else{alert("Error: "+(await r.json()).error);}
};
load();
</script></body></html>
```

Deploy:
```bash
mkdir -p /home/azcweb/web/doors.azc.com.co/public_html/portal
for f in login index users; do
  scp ui/portal-$f.html  # a /root/uhppoted-dashboard/ui/
  cp /root/uhppoted-dashboard/ui/portal-$f.html /home/azcweb/web/doors.azc.com.co/public_html/portal/$f.html
done
chown -R azcweb:azcweb /home/azcweb/web/doors.azc.com.co/public_html/portal
```

- [ ] **Step 2: Crear usuarios reales de prueba (Gisella, Johan) vía la API con sesión admin**

```bash
curl -sk -c /tmp/ck.txt -X POST https://doors.azc.com.co/auth/login -d '{"username":"koichi","password":"<contrasena-bootstrap-temporal>"}' -o /dev/null
curl -sk -b /tmp/ck.txt -X POST https://doors.azc.com.co/auth/users -d '{"username":"gisella","name":"Gisella","password":"Gis.2026","caps":["ver_eventos"]}' -w "\ncrear gisella %{http_code}\n"
curl -sk -b /tmp/ck.txt -X POST https://doors.azc.com.co/auth/users -d '{"username":"johan","name":"Johan","password":"Joh.2026","caps":["editar_tarjetas","editar_horarios"]}' -w "\ncrear johan %{http_code}\n"
```

- [ ] **Step 3: Verificar RBAC end-to-end (cada quien ve solo lo suyo)**

```bash
# Gisella: ve eventos (200), NO edita tarjetas (403), NO gestiona usuarios (403)
curl -sk -c /tmp/gis.txt -X POST https://doors.azc.com.co/auth/login -d '{"username":"gisella","password":"Gis.2026"}' -o /dev/null
curl -sk -b /tmp/gis.txt -o /dev/null -w "gisella eventos -> %{http_code} (200)\n" "https://doors.azc.com.co/analytics/api/events?page_size=1"
curl -sk -b /tmp/gis.txt -o /dev/null -w "gisella tarjetas -> %{http_code} (403)\n" "https://doors.azc.com.co/schedules/api/cards-list"
curl -sk -b /tmp/gis.txt -o /dev/null -w "gisella users -> %{http_code} (403)\n" "https://doors.azc.com.co/auth/users"
# Johan: edita horarios/tarjetas (200), NO eventos-dashboard kpis (403), NO abre puerta (403)
curl -sk -c /tmp/joh.txt -X POST https://doors.azc.com.co/auth/login -d '{"username":"johan","password":"Joh.2026"}' -o /dev/null
curl -sk -b /tmp/joh.txt -o /dev/null -w "johan horarios -> %{http_code} (200)\n" "https://doors.azc.com.co/schedules/api/profiles"
curl -sk -b /tmp/joh.txt -o /dev/null -w "johan kpis -> %{http_code} (403)\n" "https://doors.azc.com.co/analytics/api/kpis"
curl -sk -b /tmp/joh.txt -o /dev/null -w "johan abrir-puerta -> %{http_code} (403)\n" "https://doors.azc.com.co/door-opener/open-door"
# páginas del portal
curl -sk -o /dev/null -w "login page -> %{http_code} (200 publica)\n" "https://doors.azc.com.co/portal/login.html"
```
Expected exactamente: gisella eventos 200, tarjetas 403, users 403; johan horarios 200, kpis 403, abrir-puerta 403; login page 200.

- [ ] **Step 4: Render visual del portal**

Abrir `https://doors.azc.com.co/portal/` (si no hay sesión, redirige al login). Loguear como koichi → ver todas las tiles. Loguear como gisella → ver solo "Eventos". Como johan → "Horarios" y "Tarjetas". (O screenshots con la herramienta de browser.)

- [ ] **Step 5: Commit**

```bash
cd /root/uhppoted-dashboard && git add -A && git commit -m "feat: portal UI (login, landing por capacidades, gestion de usuarios) + RBAC verificado"
```

---

## Self-Review (hecho)

**1. Cobertura:** login por usuario + permisos granulares (checklist) → portal_auth + doors-auth + UI usuarios. Portal único con pestañas por capacidad → portal-index. Gating de TODAS las piezas (analytics/schedules/door-opener) → nginx auth_request + required_cap. Cierra el hueco `/schedules/` y `/door-opener/` → Task 4. Reemplaza basic-auth compartido → Task 4. "Desde users asignar permisos" → Task 5 users.html. Panel nativo aparte para super-admin → tile `*`, `location /` intacto. Ejemplos Gisella/Johan verificados → Task 5 Step 3.

**2. Placeholders:** ninguno. Passwords temporales (`<contrasena-bootstrap-temporal>`, etc.) son reales de bootstrap, a cambiar desde la UI. Todo el código concreto.

**3. Consistencia:** `required_cap`/`user_has_cap`/`verify_token` idénticos entre portal_auth (Task 1), el servicio (Task 3) y el gating (Task 4). Capacidades (8 claves) idénticas en `CAPS` (Task 2), el mapa `required_cap` (Task 1), las tiles del portal y el checklist (Task 5). Endpoints `/auth/*` consistentes entre servicio (Task 3), nginx (Task 4) y UI (Task 5). Cookie `doors_session` consistente.

## Riesgos
- **Editar el vhost de prod** puede tumbar el sitio → backup + `nginx -t` + rollback (Task 4).
- **auth_request a un servicio caído** = 500 en todo lo gateado → `doors-auth` con `Restart=on-failure`; si se cae, las áreas devuelven 500 (no abren acceso — falla cerrado). Verificar `is-active` tras deploy.
- **door-opener tras auth**: el botón del panel nativo llama `/door-opener/` por fetch; ahora exige sesión del portal con `abrir_puerta`. Si el panel se usa con el login nativo (distinto), el botón podría requerir también sesión del portal. Validar con Koichi el flujo de apertura desde el panel (posible ajuste: que el panel use el portal, o excepción por IP LAN para el door-opener del panel).

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

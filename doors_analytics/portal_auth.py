import base64
import hashlib
import hmac
import json
import os
import secrets
import urllib.parse

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


def _normalize_path(uri):
    u = uri.split("?", 1)[0]
    prev = None
    while prev != u:                      # decodificar %xx repetido (anti doble-encoding)
        prev = u
        u = urllib.parse.unquote(u)
    parts = []
    for seg in u.split("/"):
        if seg == "" or seg == ".":
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


_RULES = [
    ("/analytics/api/kpis", "ver_dashboard"),
    ("/analytics/api/events", "ver_eventos"),
    ("/analytics/dashboard", "ver_dashboard"),
    ("/analytics/api", "ver_eventos"),
    ("/analytics", "ver_eventos"),
    ("/schedules/api/profile", "editar_horarios"),
    ("/schedules/api/card", "editar_tarjetas"),
    ("/schedules/api/bulk-assign", "editar_tarjetas"),
    ("/schedules/api/doors", "editar_tarjetas"),
    ("/schedules/api/publish", "publicar_acl"),
    ("/schedules/api/generate-tsv", "publicar_acl"),
    ("/schedules/api/role-profiles", "publicar_acl"),
    ("/schedules/api/controllers", "gestionar_controladores"),
    ("/schedules/api/teq-events", "ver_eventos"),
    ("/schedules/api", "editar_tarjetas"),   # fail-closed: API de schedules no mapeada exige write
    ("/schedules", "sesion"),
    ("/door-opener", "abrir_puerta"),
]


def required_cap(uri):
    p = _normalize_path(uri)
    for base, cap in _RULES:
        if p.startswith(base):
            return cap
    return "sesion"


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


def can_assign_caps(caller_caps, caller_username, target_username, requested_caps):
    # solo un super-admin ('*') puede otorgar '*'
    if "*" in (requested_caps or []) and "*" not in caller_caps:
        return False
    # un no-super-admin no puede editar sus PROPIAS capacidades (evita auto-escalada)
    if target_username == caller_username and "*" not in caller_caps:
        return False
    return True


def can_delete_user(caller_caps, target_caps, star_users_after_delete):
    # borrar un super-admin exige ser super-admin y no dejar el sistema sin ninguno
    if "*" in (target_caps or []):
        if "*" not in caller_caps:
            return False
        if star_users_after_delete < 1:
            return False
    return True

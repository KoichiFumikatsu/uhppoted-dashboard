#!/usr/bin/env python3
"""HTTPS REST microservice for managing uhppoted time profiles and card permissions.

Wraps `uhppote-cli` so the UI can CRUD profiles and assign them per card+door
without ever bypassing the controller as source of truth.

Endpoints:
  GET  /api/profiles
  GET  /api/profile/<id>
  PUT  /api/profile/<id>          body: {from, to, weekdays[], segments[[start,end]...], linked}
  DELETE /api/profile/<id>
  GET  /api/card/<card>
  PUT  /api/card/<card>           body: {from, to, doors: {"1": "Y"|"N"|<profile_id>, ...}}
  POST /api/bulk-assign           body: {cards:[], door:1, profile:2}
  GET  /api/cards-list            (from server cards.json, with current per-door perms)
  GET  /api/doors                 (from server doors.json)
"""
import http.server
import json
import ssl
import subprocess
import sys
import threading
import sqlite3
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, "/root/uhppoted-dashboard")
from doors_analytics import config as _cfg
from doors_analytics import db as _db

PORT = 8446
CERT = '/etc/uhppoted/httpd/uhppoted.cert'
KEY = '/etc/uhppoted/httpd/uhppoted.key'
CONTROLLER = '222451671'
CLI = '/usr/local/bin/uhppote-cli'
CARDS_JSON = Path('/var/uhppoted/httpd/system/cards.json')
DOORS_JSON = Path('/var/uhppoted/httpd/system/doors.json')
LOGS_JSON = Path('/var/uhppoted/httpd/system/logs.json')
DOOR_NAMES_JSON = Path('/var/uhppoted/analytics/door-names.json')

WEEKDAYS_CLI = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _audit(actor, action, target, details, result):
    """Best-effort portal-action audit into doors.db. Never raises."""
    try:
        c = sqlite3.connect(_cfg.DB_PATH, timeout=5)
        try:
            c.execute("PRAGMA busy_timeout=5000")
            _db.insert_audit(c, actor, action, target, details, result)
        finally:
            c.close()
    except Exception:
        pass


def _run(args, timeout=8):
    """Run uhppote-cli and return stdout text."""
    try:
        r = subprocess.run([CLI] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'


_WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _parse_profile_singular(line):
    """Parse one-line: '222451671  2 2026-05-29:2027-12-31 Mon,Tue,...,Fri 07:00-19:00 0'"""
    parts = line.split()
    if len(parts) < 4:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    dates = parts[2]
    weekdays = parts[3].split(',') if parts[3] not in ('-', '') else []
    weekdays = [w[:3] for w in weekdays]  # normalize Thurs -> Thu
    segments = []
    linked = 0
    if len(parts) > 4:
        for seg in parts[4].split(','):
            seg = seg.strip()
            if '-' in seg:
                a, b = seg.split('-')
                if a != '00:00' or b != '00:00':
                    segments.append([a, b])
    if len(parts) > 5:
        try:
            linked = int(parts[5])
        except ValueError:
            pass
    f, t = dates.split(':') if ':' in dates else (dates, dates)
    return {'id': pid, 'from': f, 'to': t, 'weekdays': weekdays,
            'segments': segments, 'linked': linked}


def _parse_profile_table_row(line):
    """Parse table row from get-time-profiles:
    '2  2026-05-29 2027-12-31  Y Y Y Y Y N N  07:00 19:00  00:00 00:00  00:00 00:00  0'
    Columns: id, from, to, mon, tue, wed, thu, fri, sat, sun, s1, e1, s2, e2, s3, e3, [linked]
    """
    parts = line.split()
    if len(parts) < 16:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    f = parts[1]
    t = parts[2]
    weekday_flags = parts[3:10]  # 7 days
    weekdays = [_WEEKDAY_NAMES[i] for i, v in enumerate(weekday_flags) if v == 'Y']
    times = parts[10:16]  # 3 pairs
    segments = []
    for i in range(0, 6, 2):
        s, e = times[i], times[i + 1]
        if s != '00:00' or e != '00:00':
            segments.append([s, e])
    linked = 0
    if len(parts) >= 17:
        try:
            linked = int(parts[16])
        except ValueError:
            pass
    return {'id': pid, 'from': f, 'to': t, 'weekdays': weekdays,
            'segments': segments, 'linked': linked}


def _parse_profile_line(line):
    """Try tabular first (multi-profile list), fall back to singular."""
    p = _parse_profile_table_row(line)
    return p if p else _parse_profile_singular(line)


def _parse_card_line(line):
    """Parse: '222451671  17055142 2026-01-13 2099-12-31 Y Y Y Y'
    Each door slot can be 'Y' / 'N' / numeric profile id."""
    parts = line.split()
    if len(parts) < 5:
        return None
    return {
        'controller': int(parts[0]),
        'card': int(parts[1]),
        'from': parts[2],
        'to': parts[3],
        'doors': {str(i + 1): parts[4 + i] for i in range(min(4, len(parts) - 4))},
    }


def _is_deleted(p):
    """Profile marcado como borrado: rango sentinela 2020-01-01:2020-01-02 sin segmentos."""
    return p.get('from') == '2020-01-01' and p.get('to') == '2020-01-02' and not p.get('segments')


def api_get_profiles():
    rc, out, err = _run(['get-time-profiles', CONTROLLER])
    if rc != 0:
        return 500, {'error': err or 'cli failed'}
    profiles = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith('-') or line.lower().startswith('time profiles') or line.lower().startswith('profile'):
            continue
        p = _parse_profile_line(line)
        if p and not _is_deleted(p):
            profiles.append(p)
    return 200, {'profiles': profiles}


def api_get_profile(pid):
    rc, out, err = _run(['get-time-profile', CONTROLLER, str(pid)])
    if rc != 0:
        return 404, {'error': 'not found'}
    for line in out.splitlines():
        p = _parse_profile_line(line.strip())
        if p:
            return 200, p
    return 404, {'error': 'parse failed'}


def api_put_profile(pid, body):
    """body: {from, to, weekdays[Mon,Tue,..], segments[[start,end],...], linked}"""
    f = body.get('from', '2026-01-01')
    t = body.get('to', '2099-12-31')
    weekdays = body.get('weekdays', [])
    segments = body.get('segments', [])
    linked = body.get('linked', 0)

    if not (2 <= int(pid) <= 254):
        return 400, {'error': 'profile id must be 2..254'}

    wk = ','.join(weekdays) if weekdays else 'Mon,Tue,Wed,Thu,Fri,Sat,Sun'
    # max 3 segments, default 00:00-00:00
    segs = []
    for s in segments[:3]:
        if len(s) == 2:
            segs.append(f'{s[0]}-{s[1]}')
        else:
            segs.append('00:00-00:00')
    while len(segs) < 3:
        segs.append('00:00-00:00')
    seg_arg = ','.join(segs)

    rc, out, err = _run(['set-time-profile', CONTROLLER, str(pid),
                         f'{f}:{t}', wk, seg_arg, str(linked)])
    if rc != 0:
        return 500, {'error': err or out or 'cli failed'}
    return 200, {'ok': True, 'message': out}


def api_delete_profile(pid):
    """uhppote-cli has no per-profile delete; we set it to all-zero."""
    rc, out, err = _run(['set-time-profile', CONTROLLER, str(pid),
                         '2020-01-01:2020-01-02', 'Mon', '00:00-00:00', '0'])
    if rc != 0:
        return 500, {'error': err or 'cli failed'}
    return 200, {'ok': True}


def api_get_card(card):
    rc, out, err = _run(['get-card', CONTROLLER, str(card)])
    if rc != 0:
        return 404, {'error': 'card not found'}
    for line in out.splitlines():
        c = _parse_card_line(line.strip())
        if c:
            return 200, c
    return 404, {'error': 'parse failed'}


def api_put_card(card, body):
    """body: {from, to, doors: {"1": "Y"/"N"/<int>, ...}}"""
    f = body.get('from', '2026-01-01')
    t = body.get('to', '2099-12-31')
    doors = body.get('doors', {})
    parts = []
    for d in ['1', '2', '3', '4']:
        v = doors.get(d, 'N')
        if v == 'Y':
            parts.append(d)
        elif v == 'N':
            pass
        else:
            try:
                pid = int(v)
                if 2 <= pid <= 254:
                    parts.append(f'{d}:{pid}')
            except (ValueError, TypeError):
                pass
    doors_arg = ','.join(parts) if parts else ''
    rc, out, err = _run(['put-card', CONTROLLER, str(card), f, t, doors_arg])
    if rc != 0:
        return 500, {'error': err or out or 'cli failed'}
    return 200, {'ok': True, 'message': out}


def api_bulk_assign(body):
    """body: {cards:[card1, card2,...], door:1, profile:2 (or 'Y'/'N')}"""
    cards = body.get('cards', [])
    door = str(body.get('door', '1'))
    profile = body.get('profile')

    if door not in ('1', '2', '3', '4'):
        return 400, {'error': 'door must be 1..4'}

    results = []
    for card in cards:
        # Read current card permissions
        code, current = api_get_card(card)
        if code != 200:
            results.append({'card': card, 'ok': False, 'error': 'not found'})
            continue
        doors = current['doors'].copy()
        doors[door] = profile  # overwrite this door's permission
        code2, resp = api_put_card(card, {
            'from': current['from'],
            'to': current['to'],
            'doors': doors,
        })
        results.append({'card': card, 'ok': code2 == 200, 'response': resp})
    return 200, {'results': results}


def api_cards_list():
    if not CARDS_JSON.exists():
        return 200, {'cards': []}
    with open(CARDS_JSON) as f:
        data = json.load(f)
    out = []
    for c in data.get('cards', []):
        # only basic info; per-door permissions queried on demand via /api/card/<n>
        out.append({
            'card': c.get('card'),
            'from': c.get('from'),
            'to': c.get('to'),
            'groups': c.get('groups', []),
        })
    return 200, {'cards': out}


def _door_name_overrides():
    if not DOOR_NAMES_JSON.exists():
        return {}
    try:
        with open(DOOR_NAMES_JSON) as f:
            return json.load(f)
    except Exception:
        return {}


def api_doors():
    if not DOORS_JSON.exists():
        return 200, {'doors': []}
    with open(DOORS_JSON) as f:
        data = json.load(f)
    doors = data.get('doors', [])
    ov = _door_name_overrides()
    for d in doors:
        oid = str(d.get('OID', ''))
        if oid in ov:
            d['name'] = ov[oid]
    return 200, {'doors': doors}


def api_set_door_name(oid, body):
    name = (body.get('name') or '').strip()
    if not name:
        return 400, {'error': 'nombre vacio'}
    ov = _door_name_overrides()
    ov[str(oid)] = name
    try:
        DOOR_NAMES_JSON.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(DOOR_NAMES_JSON) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(ov, f, indent=1, ensure_ascii=False)
        Path(tmp).replace(DOOR_NAMES_JSON)
    except Exception as e:
        return 500, {'error': str(e)}
    return 200, {'ok': True, 'oid': str(oid), 'name': name}


def api_logs(qs):
    """Passthrough del log de auditoría del panel nativo (logs.json)."""
    if not LOGS_JSON.exists():
        return 200, {'rows': [], 'count': 0}
    try:
        with open(LOGS_JSON) as f:
            data = json.load(f)
    except Exception as e:
        return 500, {'error': str(e)}
    rows = data.get('logs', data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        rows = list(rows.values()) if isinstance(rows, dict) else []
    frm = (qs.get('from', [None])[0]) or None
    to = (qs.get('to', [None])[0]) or None
    if frm:
        rows = [r for r in rows if str(r.get('timestamp', '')) >= frm]
    if to:
        rows = [r for r in rows if str(r.get('timestamp', '')) <= to + '~']
    rows.sort(key=lambda r: str(r.get('timestamp', '')), reverse=True)
    try:
        limit = min(int((qs.get('limit', ['500'])[0]) or 500), 5000)
    except ValueError:
        limit = 500
    return 200, {'rows': rows[:limit], 'count': len(rows[:limit])}


# ---- ACL generator / publish (B-desde-panel) ----
import datetime
CONF = Path('/etc/uhppoted/uhppoted.conf')
GROUPS_JSON = Path('/var/uhppoted/httpd/system/groups.json')
CONTROLLERS_JSON = Path('/var/uhppoted/httpd/system/controllers.json')
ACL_EXTRA_CONTROLLERS_JSON = Path('/etc/uhppoted/acl-extra-controllers.json')
CARD_PINS_JSON = Path('/etc/uhppoted/card-pins.json')
ROLE_PROFILES_JSON = Path('/etc/uhppoted/role-profiles.json')
ACL_DIR = Path('/var/uhppoted/acl')
ROLE_PRIORITY = ['Directivos', 'Administrativos', 'Servicios Generales',
                 'Empleados', 'Invitados', 'Caso Especial', 'Retirados']
_DEFAULT_ROLE_PROFILES = {
    'Directivos': 'Y', 'Administrativos': 'Y', 'Servicios Generales': 'Y',
    'Empleados': 'Y', 'Invitados': 'Y', 'Caso Especial': 'Y', 'Retirados': 'N',
}


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _door_labels():
    """Parse uhppoted.conf -> {(serial, door_num): label}."""
    labels = {}
    if not CONF.exists():
        return labels
    for line in CONF.read_text().splitlines():
        line = line.strip()
        if line.startswith('UT0311-L0x.') and '.door.' in line and '=' in line:
            key, val = line.split('=', 1)
            parts = key.strip().split('.')
            try:
                labels[(parts[1], parts[3])] = val.strip()
            except IndexError:
                pass
    return labels


def _controllers():
    data = _load_json(CONTROLLERS_JSON, {})
    cs = data.get('controllers', []) if isinstance(data, dict) else data
    extra = _load_json(ACL_EXTRA_CONTROLLERS_JSON, {})
    ecs = extra.get('controllers', []) if isinstance(extra, dict) else extra
    out, seen = [], set()
    for x in list(cs) + list(ecs):
        serial = str(x.get('device-id') or x.get('deviceID') or x.get('serial'))
        if serial in seen:
            continue
        seen.add(serial)
        out.append({'serial': serial,
                    'doors': {str(k): v for k, v in (x.get('doors') or {}).items()}})
    return out


def _groups():
    data = _load_json(GROUPS_JSON, {})
    gs = data.get('groups', []) if isinstance(data, dict) else data
    out = {}
    for x in gs:
        doors = x.get('doors', [])
        if isinstance(doors, dict):
            doors = [k for k, v in doors.items() if v]
        out[x.get('OID')] = {'name': x.get('name', ''), 'doors': set(doors)}
    return out


def _role_profiles():
    rp = _load_json(ROLE_PROFILES_JSON, None)
    if rp is None:
        ROLE_PROFILES_JSON.write_text(json.dumps(_DEFAULT_ROLE_PROFILES, ensure_ascii=False, indent=2))
        return dict(_DEFAULT_ROLE_PROFILES)
    return rp


def api_groups():
    groups = _groups()  # {OID: {name, doors:set}}
    counts = {}
    cdata = _load_json(CARDS_JSON, {})
    for c in (cdata.get('cards', []) if isinstance(cdata, dict) else []):
        for g in (c.get('groups') or []):
            counts[g] = counts.get(g, 0) + 1
    ddata = _load_json(DOORS_JSON, {})
    dmap = {}
    for d in (ddata.get('doors', []) if isinstance(ddata, dict) else []):
        dmap[d.get('OID')] = d.get('name', d.get('OID'))
    out = []
    for oid, g in groups.items():
        out.append({'oid': oid, 'name': g['name'],
                    'doors': sorted(dmap.get(d, d) for d in g['doors']),
                    'cards': counts.get(oid, 0)})
    out.sort(key=lambda x: x['name'])
    return 200, {'groups': out}


def _cell_for_door(group_oids, door_oid, groups, role_profiles):
    granting = [g for g in group_oids if door_oid in groups.get(g, {}).get('doors', set())]
    if not granting:
        return 'N'
    def prio(g):
        name = groups.get(g, {}).get('name', '')
        return ROLE_PRIORITY.index(name) if name in ROLE_PRIORITY else 999
    granting.sort(key=prio)
    name = groups.get(granting[0], {}).get('name', '')
    val = role_profiles.get(name, 'Y')
    return str(val) if val not in (None, '') else 'Y'


def generate_acl_tsv(only_serial=None):
    labels = _door_labels()
    controllers = _controllers()
    if only_serial:
        controllers = [c for c in controllers if c['serial'] == str(only_serial)]
    groups = _groups()
    role_profiles = _role_profiles()
    cards = _load_json(CARDS_JSON, {})
    cards = cards.get('cards', []) if isinstance(cards, dict) else cards

    columns = []
    for c in controllers:
        for num in sorted(c['doors'].keys()):
            oid = c['doors'][num]
            label = labels.get((c['serial'], num), f"{c['serial']} door {num}")
            columns.append((label, oid))

    pins = _load_json(CARD_PINS_JSON, {})
    overrides = _card_overrides()
    header = ['Card Number', 'PIN', 'From', 'To'] + [lbl for lbl, _ in columns]
    rows = [header]
    for cd in cards:
        cardnum = cd.get('card')
        if not cardnum:
            continue
        frm = cd.get('from', '2024-01-01')
        to = cd.get('to', '2099-12-31')
        gids = cd.get('groups') or []
        pin = str(pins.get(str(cardnum), '') or '')
        ov = (overrides.get(str(cardnum)) or {}).get('doors') or {}
        cells = [str(ov.get(oid, _cell_for_door(gids, oid, groups, role_profiles)))
                 for _, oid in columns]
        rows.append([str(cardnum), pin, frm, to] + cells)

    tsv = '\n'.join('\t'.join(r) for r in rows) + '\n'
    return tsv, header, len(rows) - 1


# ---- Permisos por tarjeta sobre las 16 puertas (override portal-owned + push) ----
CARD_DOOR_OVERRIDES_JSON = Path('/var/uhppoted/analytics/card-door-overrides.json')
PALMETTO = '222451671'


def _card_overrides():
    return _load_json(CARD_DOOR_OVERRIDES_JSON, {})


def _save_card_overrides(ov):
    CARD_DOOR_OVERRIDES_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(CARD_DOOR_OVERRIDES_JSON) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(ov, f, indent=1, ensure_ascii=False)
    Path(tmp).replace(CARD_DOOR_OVERRIDES_JSON)


def _last_publish_ts():
    try:
        st = (ACL_DIR / 'latest.tsv').stat()
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')


def _door_oid_names():
    names = {}
    ddata = _load_json(DOORS_JSON, {})
    for d in (ddata.get('doors', []) if isinstance(ddata, dict) else []):
        names[str(d.get('OID'))] = d.get('name', '')
    names.update(_door_name_overrides())
    return names


def api_card_doors(card):
    """Matriz de las 16 puertas para una tarjeta: valor del grupo, override y efectivo."""
    cards = _load_json(CARDS_JSON, {})
    cards = cards.get('cards', []) if isinstance(cards, dict) else cards
    cd = next((c for c in cards if str(c.get('card')) == str(card)), None)
    if cd is None:
        return 404, {'error': 'la tarjeta no esta en el panel'}
    groups = _groups()
    role_profiles = _role_profiles()
    gids = cd.get('groups') or []
    ent = _card_overrides().get(str(card)) or {}
    ov = ent.get('doors') or {}
    names = _door_oid_names()
    # nombre de placa resuelto aca: /api/controllers-names exige otra capacidad
    cnames = dict(CONTROLLERS_META)
    cnames.update(_name_overrides())
    rows = []
    for c in _controllers():
        for num in sorted(c['doors'], key=int):
            oid = str(c['doors'][num])
            grp = _cell_for_door(gids, oid, groups, role_profiles)
            rows.append({'serial': c['serial'],
                         'ctrl_name': cnames.get(c['serial'], c['serial']),
                         'number': str(num), 'oid': oid,
                         'name': names.get(oid) or ('Puerta ' + str(num)),
                         'group_value': grp, 'override': ov.get(oid),
                         'value': str(ov.get(oid, grp))})
    pub = _last_publish_ts()
    return 200, {'card': str(card), 'from': cd.get('from'), 'to': cd.get('to'),
                 'groups': gids, 'doors': rows, 'last_publish': pub,
                 'pending_publish': bool(ent.get('ts') and (pub is None or ent['ts'] > pub))}


def api_put_card_doors(card, body):
    """Guarda el override y aplica Palmetto de una (LAN); Teq queda pendiente de Publicar."""
    valid = {}
    for c in _controllers():
        for num, oid in c['doors'].items():
            valid[str(oid)] = (c['serial'], str(num))
    clean = {}
    for oid, v in (body.get('doors') or {}).items():
        oid, v = str(oid), str(v)
        if oid not in valid:
            return 400, {'error': 'puerta desconocida: ' + oid}
        if v not in ('Y', 'N'):
            try:
                pid = int(v)
            except ValueError:
                return 400, {'error': 'valor invalido en %s: %s' % (oid, v)}
            if not 2 <= pid <= 254:
                return 400, {'error': 'profile fuera de rango en ' + oid}
        clean[oid] = v

    ov = _card_overrides()
    if clean:
        ov[str(card)] = {'doors': clean, 'ts': _now_str()}
    else:
        ov.pop(str(card), None)
    _save_card_overrides(ov)

    code, eff = api_card_doors(card)
    if code != 200:
        return code, eff
    pdoors = {r['number']: r['value'] for r in eff['doors'] if r['serial'] == PALMETTO}
    if pdoors:
        # from/to no tienen override: van al hardware pero el panel los repone al publicar
        c2, r2 = api_put_card(card, {'from': body.get('from') or eff['from'],
                                     'to': body.get('to') or eff['to'], 'doors': pdoors})
        if c2 != 200:
            return 500, {'error': 'override guardado pero fallo el push a Palmetto: %s'
                                  % r2.get('error', ''), 'saved': True}
    teq = sorted({valid[o][0] for o in clean if valid[o][0] != PALMETTO})
    return 200, {'ok': True, 'card': str(card), 'palmetto_applied': bool(pdoors),
                 'pending_publish': teq}


def api_get_role_profiles():
    groups = _groups()
    names = [g['name'] for g in groups.values()]
    rp = _role_profiles()
    for n in names:
        rp.setdefault(n, 'Y')
    return 200, {'roleProfiles': rp, 'roles': names}


def api_put_role_profiles(body):
    rp = body.get('roleProfiles', body)
    if not isinstance(rp, dict):
        return 400, {'error': 'roleProfiles must be an object'}
    ROLE_PROFILES_JSON.write_text(json.dumps(rp, ensure_ascii=False, indent=2))
    return 200, {'ok': True, 'roleProfiles': rp}


def api_generate_tsv():
    tsv, header, n = generate_acl_tsv()
    return 200, {'tsv': tsv, 'header': header, 'cards': n}


import re as _re

PUBLISH_STATUS_JSON = Path('/var/uhppoted/publish-status.json')
PER_CTRL_CONF_DIR = Path('/var/uhppoted/per-ctrl-conf')
# Orden de publicación: placas confiables primero, la .150 (flaky) al final.
PUBLISH_ORDER = ['222451671', '225088590', '425036574', '223205300', '423150802']
PUBLISH_RETRIES = {'423150802': 8, '223205300': 12, '425036574': 12}
PUBLISH_NAMES = {'222451671': 'Palmetto', '223205300': 'Tequendama .13',
                 '225088590': 'Tequendama .125', '423150802': 'Tequendama .150',
                 '425036574': 'Tequendama .12'}
_publish_lock = threading.Lock()
_publishing = {'on': False}


def _isolated_conf(serial):
    PER_CTRL_CONF_DIR.mkdir(parents=True, exist_ok=True)
    path = PER_CTRL_CONF_DIR / f'{serial}.toml'
    out = []
    for line in CONF.read_text().splitlines():
        if line.startswith('UT0311-L0x.'):
            if line.startswith(f'UT0311-L0x.{serial}.'):
                out.append(line)
        else:
            out.append(line)
    path.write_text('\n'.join(out) + '\n')
    return path


def _warmup_ctrl(serial, conf):
    for _ in range(10):
        rc, out, _e = _run(['--config', str(conf), '--bind', '0.0.0.0:0',
                            '--timeout', '2s', 'get-device', serial], timeout=5)
        if rc == 0 and serial in out:
            return True
    return False


def _parse_loadacl(text):
    m = {}
    for k in ('added', 'updated', 'deleted', 'unchanged', 'errored'):
        r = _re.search(k + r'[:\s]+(\d+)', text)
        if r:
            m[k] = int(r.group(1))
    return m


def _load_one(serial):
    conf = _isolated_conf(serial)
    tsv, _h, _n = generate_acl_tsv(only_serial=serial)
    ACL_DIR.mkdir(parents=True, exist_ok=True)
    tsvpath = ACL_DIR / f'publish-{serial}.tsv'
    tsvpath.write_text(tsv)
    retries = PUBLISH_RETRIES.get(serial, 3)
    last = ''
    for attempt in range(1, retries + 1):
        _warmup_ctrl(serial, conf)
        rc, out, err = _run(['--config', str(conf), '--bind', '0.0.0.0:0',
                            '--timeout', '20s', 'load-acl', '--with-pin', str(tsvpath)],
                            timeout=300)
        last = (out + ' ' + err).strip()
        low = last.lower()
        is_fail = ('i/o timeout' in low) or ('error: [' in low) or ('refused' in low)
        has_summary = ('unchanged:' in low) or ('updated:' in low)
        if rc == 0 and not is_fail and has_summary:
            return {'ok': True, 'attempt': attempt, 'summary': _parse_loadacl(last)}
    return {'ok': False, 'attempts': retries, 'error': last[-240:]}


def _write_publish_status(s):
    tmp = str(PUBLISH_STATUS_JSON) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(s, f, ensure_ascii=False)
    Path(tmp).replace(PUBLISH_STATUS_JSON)


def _publish_worker(targets):
    # El keepalive contiende con la lectura bulk del load-acl (la placa atiende 1 req a la vez).
    # Se pausa durante el publish y se reanuda al final.
    subprocess.run(['systemctl', 'stop', 'teq-keepalive'], capture_output=True)
    rows = [{'serial': s, 'name': PUBLISH_NAMES.get(s, s), 'status': 'pending',
             'retries': PUBLISH_RETRIES.get(s, 3)} for s in targets]

    def flush(running=True):
        _write_publish_status({'controllers': rows, 'running': running,
                               'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    flush()
    for i, s in enumerate(targets):
        rows[i]['status'] = 'running'
        flush()
        res = _load_one(s)
        rows[i]['status'] = 'ok' if res.get('ok') else 'failed'
        rows[i].update(res)
        flush()
    flush(running=False)
    subprocess.run(['systemctl', 'start', 'teq-keepalive'], capture_output=True)
    with _publish_lock:
        _publishing['on'] = False


def api_publish(body):
    targets = (body or {}).get('controllers') or PUBLISH_ORDER
    with _publish_lock:
        if _publishing['on']:
            return 200, {'started': False, 'running': True}
        _publishing['on'] = True
    threading.Thread(target=_publish_worker, args=(targets,), daemon=True).start()
    return 200, {'started': True, 'running': True, 'controllers': targets}


def api_publish_status():
    return 200, _load_json(PUBLISH_STATUS_JSON, {'controllers': [], 'running': False})


# ---- end ACL generator ----


TEQ_EVENTS_JSON = Path('/var/uhppoted/teq-events.json')


def api_teq_events():
    try:
        d = json.loads(TEQ_EVENTS_JSON.read_text())
        evs = sorted(d.get('events', []), key=lambda x: x.get('timestamp', ''), reverse=True)[:200]
        return 200, {'events': evs, 'cursor': d.get('cursor', {})}
    except (FileNotFoundError, json.JSONDecodeError):
        return 200, {'events': [], 'cursor': {}}



# ---- Controladores: estado on-demand (pull secuencial 1x1, no httpd) ----
CONTROLLERS_STATUS_JSON = Path('/var/uhppoted/controllers-status.json')
CONTROLLERS_META = [
    ('222451671', 'Palmetto'),
    ('223205300', 'Tequendama .13'),
    ('225088590', 'Tequendama .125'),
    ('423150802', 'Tequendama .150'),
    ('425036574', 'Tequendama .12'),
]
_refresh_lock = threading.Lock()
_refreshing = {'on': False}


def _now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _write_status(s):
    tmp = str(CONTROLLERS_STATUS_JSON) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(s, f, ensure_ascii=False)
    Path(tmp).replace(CONTROLLERS_STATUS_JSON)


def _pending_row(serial, name):
    return {'serial': serial, 'name': name, 'online': None, 'ip': None,
            'firmware': None, 'cards': None, 'updated': None, 'pending': True}


def _pull_one(serial, name):
    info = {'serial': serial, 'name': name, 'online': False, 'ip': None,
            'firmware': None, 'cards': None, 'updated': _now_str(), 'pending': False}
    for _ in range(6):
        rc, out, err = _run(['--timeout', '3s', 'get-device', serial], timeout=6)
        p = out.split()
        if rc == 0 and len(p) >= 6 and p[0] == serial:
            info['online'] = True
            info['ip'] = p[1]
            info['firmware'] = p[5]
            break
    if info['online']:
        best = 0
        for _ in range(3):
            rc, out, err = _run(['--timeout', '6s', 'get-cards', serial], timeout=14)
            n = len([l for l in out.splitlines() if l[:1].isdigit()])
            if n > best:
                best = n
        info['cards'] = best or None
    return info


def _refresh_worker():
    results = []
    for serial, name in CONTROLLERS_META:
        results.append(_pull_one(serial, name))
        done = {r['serial'] for r in results}
        rows = list(results) + [_pending_row(s, n) for s, n in CONTROLLERS_META if s not in done]
        _write_status({'controllers': rows, 'updated': _now_str(), 'refreshing': True})
    _write_status({'controllers': results, 'updated': _now_str(), 'refreshing': False})
    with _refresh_lock:
        _refreshing['on'] = False


# ---- Controladores editables: nombre / sync reloj / abrir puerta ----
CONTROLLER_NAMES_JSON = Path('/etc/uhppoted/controller-names.json')
SERIALS = {s for s, _ in CONTROLLERS_META}


def _name_overrides():
    try:
        return json.loads(CONTROLLER_NAMES_JSON.read_text()) if CONTROLLER_NAMES_JSON.exists() else {}
    except Exception:
        return {}


def _apply_names(status):
    ov = _name_overrides()
    for r in status.get('controllers', []):
        if r.get('serial') in ov:
            r['name'] = ov[r['serial']]
    return status


def _controller_doors():
    """{serial: [{number, oid, name}]} desde controllers.json + acl-extra + nombres reales."""
    names = {}
    data = _load_json(DOORS_JSON, {})
    for d in (data.get('doors', []) if isinstance(data, dict) else data):
        names[str(d.get('OID', ''))] = d.get('name', '')
    names.update(_door_name_overrides())
    out = {}
    for c in _controllers():
        rows = []
        for num, oid in sorted(c['doors'].items(), key=lambda kv: int(kv[0])):
            rows.append({'number': str(num), 'oid': str(oid),
                         'name': names.get(str(oid)) or ('Puerta ' + str(num))})
        out[c['serial']] = rows
    return out


def _apply_doors(status):
    dm = _controller_doors()
    for r in status.get('controllers', []):
        r['doors'] = dm.get(str(r.get('serial')), [])
    return status


def api_controllers_status():
    return 200, _apply_doors(_apply_names(_load_json(
        CONTROLLERS_STATUS_JSON,
        {'controllers': [], 'updated': None, 'refreshing': False})))


def api_controllers_names():
    return 200, {'names': _name_overrides()}


def api_set_controller_name(serial, body):
    if serial not in SERIALS:
        return 404, {'error': 'unknown controller'}
    name = (body.get('name') or '').strip()
    if not name:
        return 400, {'error': 'name required'}
    ov = _name_overrides()
    ov[serial] = name
    CONTROLLER_NAMES_JSON.write_text(json.dumps(ov, ensure_ascii=False))
    return 200, {'ok': True, 'serial': serial, 'name': name}


def api_set_time(serial):
    if serial not in SERIALS:
        return 404, {'error': 'unknown controller'}
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rc, out, err = _run(['--timeout', '6s', 'set-time', serial, now], timeout=10)
    if rc != 0:
        return 500, {'error': err or out or 'cli failed'}
    return 200, {'ok': True, 'time': now, 'result': out.strip()}


def api_open_door(serial, body):
    if serial not in SERIALS:
        return 404, {'error': 'unknown controller'}
    door = str(body.get('door', '1'))
    if door not in ('1', '2', '3', '4'):
        return 400, {'error': 'door must be 1..4'}
    mapped = {d['number'] for d in _controller_doors().get(serial, [])}
    if mapped and door not in mapped:
        return 400, {'error': 'esa puerta no existe en este controlador'}
    rc, out, err = _run(['--timeout', '6s', 'open-door', serial, door], timeout=10)
    if rc != 0:
        return 500, {'error': err or out or 'cli failed'}
    return 200, {'ok': True, 'serial': serial, 'door': door, 'result': out.strip()}


def api_controllers_refresh():
    with _refresh_lock:
        if _refreshing['on']:
            return 200, {'started': False, 'refreshing': True}
        _refreshing['on'] = True
    threading.Thread(target=_refresh_worker, daemon=True).start()
    return 200, {'started': True, 'refreshing': True}


class Handler(http.server.BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, code, body):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode('utf-8'))

    def _body(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _actor(self):
        return self.headers.get('X-Portal-User', '?')

    def _write(self, p, method, code_obj, body):
        """Send response and record a portal-action audit row for mutations."""
        code, obj = code_obj
        if code != 404:
            details = ''
            if isinstance(body, dict) and body:
                details = json.dumps(body, ensure_ascii=False)[:300]
            _audit(self._actor(), method + ' ' + p, p, details, str(code))
        return self._json(code, obj)

    def do_GET(self):
        p = urlparse(self.path).path.rstrip('/')
        qs = parse_qs(urlparse(self.path).query)
        if p in ('', '/', '/api'):
            return self._json(200, {'service': 'schedule-manager', 'controller': CONTROLLER})
        if p == '/api/profiles':
            return self._json(*api_get_profiles())
        if p.startswith('/api/profile/'):
            return self._json(*api_get_profile(p.split('/')[-1]))
        if p.startswith('/api/card-doors/'):
            return self._json(*api_card_doors(p.split('/')[-1]))
        if p.startswith('/api/card/'):
            return self._json(*api_get_card(p.split('/')[-1]))
        if p == '/api/cards-list':
            return self._json(*api_cards_list())
        if p == '/api/doors':
            return self._json(*api_doors())
        if p == '/api/groups':
            return self._json(*api_groups())
        if p == '/api/logs':
            return self._json(*api_logs(qs))
        if p == '/api/teq-events':
            return self._json(*api_teq_events())
        if p == '/api/controllers-status':
            return self._json(*api_controllers_status())
        if p == '/api/controllers-names':
            return self._json(*api_controllers_names())
        if p == '/api/publish-status':
            return self._json(*api_publish_status())
        if p == '/api/role-profiles':
            return self._json(*api_get_role_profiles())
        if p == '/api/generate-tsv':
            return self._json(*api_generate_tsv())
        return self._json(404, {'error': 'not found'})

    def do_PUT(self):
        p = urlparse(self.path).path.rstrip('/')
        body = self._body()
        if p.startswith('/api/profile/'):
            return self._write(p, 'PUT', api_put_profile(p.split('/')[-1], body), body)
        if p.startswith('/api/card-doors/'):
            return self._write(p, 'PUT', api_put_card_doors(p.split('/')[-1], body), body)
        if p.startswith('/api/card/'):
            return self._write(p, 'PUT', api_put_card(p.split('/')[-1], body), body)
        if p.startswith('/api/doors/') and p.endswith('/name'):
            return self._write(p, 'PUT', api_set_door_name(p.split('/')[3], body), body)
        if p.startswith('/api/controllers/') and p.endswith('/name'):
            return self._write(p, 'PUT', api_set_controller_name(p.split('/')[3], body), body)
        if p == '/api/role-profiles':
            return self._write(p, 'PUT', api_put_role_profiles(body), body)
        return self._json(404, {'error': 'not found'})

    def do_DELETE(self):
        p = urlparse(self.path).path.rstrip('/')
        if p.startswith('/api/profile/'):
            return self._write(p, 'DELETE', api_delete_profile(p.split('/')[-1]), {})
        return self._json(404, {'error': 'not found'})

    def do_POST(self):
        p = urlparse(self.path).path.rstrip('/')
        body = self._body()
        if p == '/api/bulk-assign':
            return self._write(p, 'POST', api_bulk_assign(body), body)
        if p == '/api/publish':
            return self._write(p, 'POST', api_publish(body), body)
        if p == '/api/controllers-refresh':
            return self._json(*api_controllers_refresh())
        if p.startswith('/api/controllers/') and p.endswith('/set-time'):
            return self._write(p, 'POST', api_set_time(p.split('/')[3]), {})
        if p.startswith('/api/controllers/') and p.endswith('/open-door'):
            return self._write(p, 'POST', api_open_door(p.split('/')[3], body), body)
        return self._json(404, {'error': 'not found'})


def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    httpd = http.server.HTTPServer(('127.0.0.1', PORT), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print(f'schedule-manager listening on https://127.0.0.1:{PORT}')
    httpd.serve_forever()


if __name__ == '__main__':
    main()

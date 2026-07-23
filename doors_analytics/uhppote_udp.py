"""Cliente UDP directo del protocolo UHPPOTE UT0311-L0x. Lee eventos de un
controlador sin pasar por el panel httpd NI por uhppote-cli, con TODOS los
campos (incluye event-type y direction, que el CLI recorta).

Mismo metodo para Palmetto (LAN) y Tequendama (unicast a su IP Tailscale, o
local desde un edge box Linux): solo cambia el host.

Protocolo (paquete de 64 bytes, verificado byte-exacto contra events.json):
  get-event, function 0xb0
  request : [0x17,0xb0,0x00,0x00, serial(4 LE), index(4 LE), 0*52]
    index 0x00000000 -> primer evento; 0xffffffff -> ultimo evento; N -> evento N
  response: [0x17,0xb0,0x00,0x00, serial(4 LE), index(4 LE),
             type(1), granted(1), door(1), direction(1), card(4 LE),
             timestamp(7 BCD: YYYYMMDDHHmmSS), reason(1), ...]
"""
import socket
import struct

PORT = 60000
_SOI = 0x17
_FN_EVENT = 0xb0


def _request(serial, index):
    pkt = bytearray(64)
    pkt[0] = _SOI
    pkt[1] = _FN_EVENT
    struct.pack_into('<I', pkt, 4, int(serial))
    struct.pack_into('<I', pkt, 8, index & 0xffffffff)
    return bytes(pkt)


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0f)


def _parse_event(serial, data):
    if len(data) < 28 or data[0] != _SOI or data[1] != _FN_EVENT:
        return None
    if struct.unpack_from('<I', data, 4)[0] != int(serial):
        return None
    index = struct.unpack_from('<I', data, 8)[0]
    if index == 0:
        return None                      # sin eventos / indice vacio
    etype, granted, door, direction = data[12], data[13], data[14], data[15]
    card = struct.unpack_from('<I', data, 16)[0]
    y = _bcd(data[20]) * 100 + _bcd(data[21])
    ts = '%04d-%02d-%02d %02d:%02d:%02d' % (
        y, _bcd(data[22]), _bcd(data[23]), _bcd(data[24]), _bcd(data[25]), _bcd(data[26]))
    reason = data[27]
    return {'index': index, 'event-type': etype, 'granted': granted == 1,
            'door': door, 'direction': direction, 'card': card,
            'timestamp': ts, 'reason': reason}


class Controller:
    def __init__(self, serial, host, port=PORT, timeout=2.5):
        self.serial = int(serial)
        self.host = host
        self.port = port
        self.timeout = timeout

    def _rpc(self, index):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.timeout)
        try:
            s.sendto(_request(self.serial, index), (self.host, self.port))
            data, _ = s.recvfrom(64)
            return _parse_event(self.serial, data)
        finally:
            s.close()

    def get_event(self, index, retries=2):
        for _ in range(retries + 1):
            try:
                return self._rpc(index)
            except socket.timeout:
                continue
        return None

    def last_index(self):
        ev = self.get_event(0xffffffff)
        return ev['index'] if ev else None

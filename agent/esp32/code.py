'''
code.py - pixellink agent for the retail ESP32 Pixel Kit (CircuitPython).

Copy this file to the root of the CIRCUITPY drive as "code.py" and
the board will run it automatically on boot/reset, connecting to WiFi
and listening for pixellink connections on port 7777.

Assumes the retail board already exposes a native `pixelkit` module
with the same shape as pixelkit.py (WIDTH, HEIGHT, set_pixel(),
render(), etc.) - that's what your CircuitPython app code already
imports as `kit`. If your retail code drives the hardware some other
way (raw neopixel/digitalio calls with no such module), this needs a
small rewrite - let me know and I'll adjust it to match.

Requires a settings.toml alongside this file with:

    CIRCUITPY_WIFI_SSID = "your-network-name"
    CIRCUITPY_WIFI_PASSWORD = "your-network-password"

Differences from the BananaPi agent (agent/pixellink_agent.py):
  - CircuitPython has no `socket`/`threading` modules - networking
    goes through `wifi` + `socketpool`, and this agent serves one
    client connection at a time (no background threads available)
  - WiFi + mDNS setup happens here instead of being pre-existing OS
    networking, so pixellink.connect("<hostname>.local") works the
    same way it does for the BananaPi variant
  - The hardware wrapper is the retail `pixelkit` module instead of
    the Linux one, but the wire protocol (newline-delimited JSON) and
    method names are identical, so pixellink.py on the host needs no
    changes at all to talk to either variant
'''

import os
import json
import wifi
import socketpool
import mdns

import pixelkit as kit

PORT     = 7777
HOSTNAME = os.getenv('PIXELLINK_HOSTNAME', 'pixelkit')


# --- WiFi + mDNS ------------------------------------------------------------

def _connect_wifi():
    ssid = os.getenv('CIRCUITPY_WIFI_SSID')
    password = os.getenv('CIRCUITPY_WIFI_PASSWORD')
    print('[pixellink] connecting to WiFi:', ssid)
    wifi.radio.connect(ssid, password)
    print('[pixellink] connected, IP:', wifi.radio.ipv4_address)

    server = mdns.Server(wifi.radio)
    server.hostname = HOSTNAME
    server.advertise_service(service_type='_pixellink', protocol='_tcp', port=PORT)
    print('[pixellink] mDNS hostname: %s.local' % HOSTNAME)


# --- method dispatch (mirrors agent/pixellink_agent.py) ---------------------

def _m_hello(p):
    return {
        'variant':     'esp32',
        'width':       kit.WIDTH,
        'height':      kit.HEIGHT,
        'has_battery': hasattr(kit, 'battery_percent') or hasattr(kit, 'battery_value'),
        'has_mic':     hasattr(kit, 'microphone_value'),
    }

def _m_render_frame(p):
    pixels = p['pixels']
    w = kit.WIDTH
    for i, rgb in enumerate(pixels):
        kit.set_pixel(i % w, i // w, tuple(rgb))
    kit.render()

def _m_beep(p):
    if hasattr(kit, 'beep'):
        kit.beep(p['frequency'], p['duration'])

METHODS = {
    'hello':        _m_hello,
    'render-frame': _m_render_frame,
    'beep':         _m_beep,
}


# --- connection handling ------------------------------------------------

def _read_message(conn, buf):
    '''
    Read one newline-delimited message from conn. buf is leftover bytes
    from the previous call (start with b''). Returns (line, new_buf) -
    line is None if the connection closed. Uses plain bytes throughout
    (concatenation + slicing only) since some CircuitPython builds
    don't support in-place bytearray slice deletion.
    '''
    chunk = bytearray(256)
    idx = buf.find(b'\n')
    while idx == -1:
        n = conn.recv_into(chunk)
        if n == 0:
            return None, buf
        buf = buf + bytes(chunk[:n])
        idx = buf.find(b'\n')
    line = buf[:idx]
    rest = buf[idx + 1:]
    return line, rest


def _handle_client(conn):
    buf = b''
    try:
        while True:
            line, buf = _read_message(conn, buf)
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            req = None
            try:
                req = json.loads(line)
                method = req.get('method')
                params = req.get('params', {}) or {}
                handler = METHODS.get(method)
                if handler is None:
                    raise ValueError('unknown method: %s' % method)
                print('[pixellink] handling', method)
                result = handler(params)
                print('[pixellink]', method, 'done')
                resp = {'id': req.get('id'), 'ok': True, 'result': result}
            except Exception as e:
                # print the real error to serial instead of only sending
                # it to the client - if the client never gets the
                # response (e.g. the socket itself is what's failing)
                # this is the only place the error is visible at all
                print('[pixellink] ERROR handling request:')
                try:
                    import traceback
                    traceback.print_exception(e, e, e.__traceback__)
                except Exception:
                    print('[pixellink]', type(e).__name__, str(e))
                resp = {'id': req.get('id') if isinstance(req, dict) else None,
                         'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e))}
            try:
                out = (json.dumps(resp) + '\n').encode('utf-8')
                conn.send(out)
            except OSError as e:
                print('[pixellink] failed to send response, connection likely dropped:', e)
                break
    except OSError as e:
        print('[pixellink] connection error:', e)
    finally:
        conn.close()


def serve():
    _connect_wifi()

    pool = socketpool.SocketPool(wifi.radio)
    srv = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
    srv.bind(('0.0.0.0', PORT))
    srv.listen(1)
    print('[pixellink] listening on port', PORT)

    # CircuitPython has no threads, so this serves one pixellink
    # connection at a time. That's fine for the normal use case (one
    # computer driving this kit) - if you need multiple simultaneous
    # controllers, that's a bigger asyncio-based rewrite.
    while True:
        conn, addr = srv.accept()
        conn.settimeout(30)  # generous - was defaulting too short, dropping
                              # the connection (EAGAIN) during any pause
                              # between requests
        print('[pixellink] client connected:', addr)
        _handle_client(conn)
        print('[pixellink] client disconnected')


serve()

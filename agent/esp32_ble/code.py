'''
code.py - pixellink agent for the retail ESP32 Pixel Kit, over BLE UART
(Nordic UART Service) instead of WiFi.

This is an ALTERNATIVE to agent/esp32/code.py, not something you run
alongside it - a board only runs one code.py. Use this one if you
want to control the kit from a computer without WiFi (or as a
convenient way to talk to it from Adafruit's Bluefruit Connect app on
a phone, for manual testing, before writing a real client).

Requires the `adafruit_ble` library on the CIRCUITPY drive (copy the
`adafruit_ble` folder from the Adafruit CircuitPython Bundle into
CIRCUITPY/lib - it is not built in).

Same wire protocol as the WiFi agent (newline-delimited JSON, same
method names) - just riding over a BLE UART stream instead of a TCP
socket. The pixellink host client would need a BLE-capable backend
(e.g. the `bleak` library on Linux/macOS/Windows) to talk to this -
that doesn't exist yet in this repo. This file on its own is useful
right now for a very direct sanity check: connect from Adafruit's
Bluefruit Connect app (UART/terminal mode), type a line like

    {"id": 1, "method": "hello", "params": {}}

and press send - you should see a JSON response come back on the
same screen. That confirms the board side is working before any
Python BLE client exists to drive it for real.
'''

import json

import microcontroller
from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

import pixelkit as kit

# Unique per-board name. BLE advertisement packets are capped at 31
# bytes total, and the Nordic UART Service's 128-bit UUID alone uses
# 18 of those (plus 3 for mandatory flags) - leaving only 8 bytes for
# the name field. "PLK-XXXX" (4 hex chars from cpu.uid) fits exactly;
# anything longer raises "Data too large for advertisement packet".
_uid_hex = ''.join('%02X' % b for b in microcontroller.cpu.uid)
BLE_NAME = 'PLK-%s' % _uid_hex[-4:]

ble = BLERadio()
uart = UARTService()
advertisement = ProvideServicesAdvertisement(uart)
advertisement.complete_name = BLE_NAME


# --- method dispatch (identical to the WiFi agent) ---------------------

def _m_hello(p):
    return {
        'variant':     'esp32-ble',
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

def _handle_line(line):
    line = line.strip()
    if not line:
        return None
    req = None
    try:
        req = json.loads(line)
        method = req.get('method')
        params = req.get('params', {}) or {}
        handler = METHODS.get(method)
        if handler is None:
            raise ValueError('unknown method: %s' % method)
        print('[pixellink-ble] handling', method)
        result = handler(params)
        print('[pixellink-ble]', method, 'done')
        return {'id': req.get('id'), 'ok': True, 'result': result}
    except Exception as e:
        print('[pixellink-ble] ERROR handling request:')
        try:
            import traceback
            traceback.print_exception(e, e, e.__traceback__)
        except Exception:
            print('[pixellink-ble]', type(e).__name__, str(e))
        return {'id': req.get('id') if isinstance(req, dict) else None,
                 'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e))}


def serve():
    while True:
        ble.start_advertising(advertisement)
        print('[pixellink-ble] advertising as "%s" - connect via Bluefruit Connect' % BLE_NAME)
        while not ble.connected:
            pass
        print('[pixellink-ble] connected')

        while ble.connected:
            line = uart.readline()
            if not line:
                continue
            resp = _handle_line(line)
            if resp is None:
                continue
            try:
                uart.write((json.dumps(resp) + '\n').encode('utf-8'))
            except OSError as e:
                print('[pixellink-ble] failed to send response:', e)
                break

        print('[pixellink-ble] disconnected')


serve()

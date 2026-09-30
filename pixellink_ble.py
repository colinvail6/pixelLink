'''
pixellink_ble.py - connect to a Pixel Kit over BLE UART (Nordic UART
Service) instead of WiFi.

Optional - requires the `bleak` package:

    pip install bleak

Talks to the same agent protocol as the WiFi client (agent/esp32_ble/
code.py on the retail board), just over BLE instead of a TCP socket.
PixelLinkKitBLE implements the same drawing API as pixellink.PixelLinkKit
(it shares the same _Canvas base class), so it works interchangeably
in a pixellink.Chain or pixellink.Group alongside WiFi-connected kits.

Usage:
    import pixellink_ble

    Kit1 = pixellink_ble.connect("PixelLinkKit-3F2A9C1B")  # by name/prefix
    Kit1.set_pixel(0, 0, (255, 0, 0))
    Kit1.render()

bleak is asyncio-based; this module runs a single background event
loop thread and bridges every call through it, so the API here stays
synchronous and consistent with the rest of pixellink.
'''

import asyncio
import itertools
import json
import threading
import time

import pixellink
from pixellink import _Canvas, PixelLinkError

try:
    from bleak import BleakClient, BleakScanner
    _HAS_BLEAK = True
except ImportError:
    _HAS_BLEAK = False

# Nordic UART Service UUIDs - same ones used by adafruit_ble's
# UARTService and Adafruit's Bluefruit Connect app.
_NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
_NUS_RX_CHAR = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'  # host writes here
_NUS_TX_CHAR = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'  # kit notifies here

# Conservative default write chunk size - the default (un-negotiated)
# BLE ATT MTU only guarantees 20 usable bytes per write. Real-world
# MTU is often negotiated higher, but starting conservative avoids
# write failures if that negotiation hasn't completed yet.
_CHUNK_SIZE = 20


class _BLELoop:
    '''
    Single shared background thread running an asyncio event loop, so
    bleak's async API can be driven from pixellink's synchronous style
    without every PixelLinkKitBLE needing its own thread/loop.
    '''
    _instance = None

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout=None):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout)

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = _BLELoop()
        return cls._instance


class PixelLinkKitBLE(_Canvas):
    '''A connection to a single Pixel Kit over BLE UART.'''

    def __init__(self, name_or_address, timeout=10.0):
        if not _HAS_BLEAK:
            raise PixelLinkError('BLE support requires the "bleak" package: pip install bleak')

        self._bg = _BLELoop.instance()
        self._notify_buf = b''
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._responses = {}
        self._resp_event = threading.Event()

        address = self._bg.run(self._resolve_address(name_or_address), timeout=timeout)
        self.host = address
        self._client = BleakClient(address)
        self._bg.run(self._client.connect(), timeout=timeout)
        self._bg.run(self._client.start_notify(_NUS_TX_CHAR, self._on_notify), timeout=timeout)

        info = self._call('hello', {}, timeout=timeout)
        self.variant     = info.get('variant', 'unknown')
        self.width       = info.get('width', 16)
        self.height      = info.get('height', 8)
        self.has_battery = info.get('has_battery', False)
        self.has_mic     = info.get('has_mic', False)

        self._pixels = [(0, 0, 0)] * (self.width * self.height)

    # -- connection setup -----------------------------------------------------

    async def _resolve_address(self, name_or_address):
        # already looks like a BLE address (MAC on Linux/Windows, UUID on macOS)
        if ':' in name_or_address or len(name_or_address) == 36:
            return name_or_address
        devices = await BleakScanner.discover(timeout=5.0)
        for d in devices:
            if d.name and (d.name == name_or_address or d.name.startswith(name_or_address)):
                return d.address
        raise PixelLinkError('no BLE device found matching "%s"' % name_or_address)

    def _on_notify(self, _handle, data):
        '''Called by bleak (on the background loop thread) as TX notifications arrive.'''
        self._notify_buf += bytes(data)
        while b'\n' in self._notify_buf:
            line, _, self._notify_buf = self._notify_buf.partition(b'\n')
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except Exception:
                continue
            with self._lock:
                self._responses[resp.get('id')] = resp
            self._resp_event.set()

    # -- transport ------------------------------------------------------------

    def _call(self, method, params, timeout=10.0):
        req_id = next(self._ids)
        msg = (json.dumps({'id': req_id, 'method': method, 'params': params}) + '\n').encode('utf-8')

        async def _send():
            for i in range(0, len(msg), _CHUNK_SIZE):
                await self._client.write_gatt_char(
                    _NUS_RX_CHAR, msg[i:i + _CHUNK_SIZE], response=False)
        self._bg.run(_send(), timeout=timeout)

        deadline = time.time() + timeout
        resp = None
        while time.time() < deadline:
            with self._lock:
                if req_id in self._responses:
                    resp = self._responses.pop(req_id)
                    break
            self._resp_event.wait(0.05)
            self._resp_event.clear()
        if resp is None:
            raise PixelLinkError('BLE request timed out waiting for a response')

        if not resp.get('ok', False):
            raise PixelLinkError(resp.get('error', 'unknown error'))
        return resp.get('result')

    def close(self):
        try:
            self._bg.run(self._client.disconnect(), timeout=5.0)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self):
        return '<PixelLinkKitBLE %s (%s) %dx%d>' % (
            self.host, self.variant, self.width, self.height)

    # -- _Canvas surface --------------------------------------------------------

    def set_pixel(self, x, y, rgb=(0, 10, 0)):
        if 0 <= x < self.width and 0 <= y < self.height:
            self._pixels[y * self.width + x] = tuple(rgb)

    def set_background(self, rgb=(255, 255, 0)):
        rgb = tuple(rgb)
        self._pixels = [rgb] * (self.width * self.height)

    def render(self):
        self._call('render-frame', {'pixels': [list(c) for c in self._pixels]})

    def beep(self, frequency, duration):
        self._call('beep', {'frequency': frequency, 'duration': duration})


def connect(name_or_address, timeout=10.0):
    '''
    Connect to a Pixel Kit over BLE UART.

    pixellink_ble.connect("PixelLinkKit-3F2A9C1B")   # by advertised name/prefix
    pixellink_ble.connect("AA:BB:CC:DD:EE:FF")        # by BLE address directly
    '''
    return PixelLinkKitBLE(name_or_address, timeout=timeout)

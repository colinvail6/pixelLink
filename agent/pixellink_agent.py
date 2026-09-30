'''
pixellink_agent.py - runs ON a Pixel Kit and exposes it to the network.

Wraps the existing pixelkit.py (BananaPi/Armbian) module so that a
pixellink client on a separate computer can drive this kit exactly as
if it were calling pixelkit.py functions directly.

Protocol: newline-delimited JSON over TCP, one request -> one response.

Request:
    {"id": 1, "method": "set-pixel", "params": {"x":0,"y":0,"rgb":[255,0,0]}}
Response:
    {"id": 1, "ok": true, "result": null}
    {"id": 1, "ok": false, "error": "..."}

Run this as a systemd service on the kit itself (BananaPi today; the
ESP32/CircuitPython port will speak the same protocol over its
existing WiFi socket support).
'''

import json
import socket
import threading
import time

import pixelkit as kit

HOST = '0.0.0.0'
PORT = 7777

_render_lock = threading.Lock()


# --- method dispatch table -----------------------------------------------

def _m_hello(p):
    return {
        'variant': 'bananapi',
        'width':   kit.WIDTH,
        'height':  kit.HEIGHT,
        'has_battery': True,
        'has_mic':     False,
    }

def _m_set_pixel(p):
    kit.set_pixel(p['x'], p['y'], tuple(p['rgb']))

def _m_set_pixel_hsv(p):
    kit.set_pixel_hsv(p['x'], p['y'], tuple(p['hsv']))

def _m_set_background(p):
    kit.set_background(tuple(p['rgb']))

def _m_clear(p):
    kit.clear()

def _m_fill_rect(p):
    kit.fill_rect(p['x'], p['y'], p['w'], p['h'], tuple(p['rgb']))

def _m_draw_rect(p):
    kit.draw_rect(p['x'], p['y'], p['w'], p['h'], tuple(p['rgb']))

def _m_draw_line(p):
    kit.draw_line(p['x0'], p['y0'], p['x1'], p['y1'], tuple(p['rgb']))

def _m_draw_letter(p):
    kit.draw_letter(p['x'], p['y'], p['char'], tuple(p['rgb']))

def _m_render(p):
    with _render_lock:
        kit.render()
        _wait_for_render_complete()

def _m_render_frame(p):
    '''
    Apply a full WIDTH*HEIGHT frame (list of [r,g,b], row-major) and
    render it in one round trip. This is what the pixellink client
    uses for all drawing - it buffers locally and only calls this
    once per frame, which is what makes smooth/chained scrolling
    possible without a network round trip per pixel.
    '''
    pixels = p['pixels']
    w = kit.WIDTH
    for i, rgb in enumerate(pixels):
        kit.set_pixel(i % w, i // w, tuple(rgb))
    with _render_lock:
        kit.render()
        _wait_for_render_complete()

def _wait_for_render_complete(timeout=2.0):
    '''
    pixelkit.py's render() is asynchronous - it waits for the *previous*
    frame's MCU ack before sending this one, but returns as soon as
    this frame's request is fired off, not once the MCU has confirmed
    it. Without this, our RPC reply goes back to the pixellink client
    (and a Chain's thread.join() completes) before the pixels have
    actually landed on the display - which is what let the ESP32 kit
    (whose render() really is synchronous) visibly race ahead of this
    one when the two are chained together.
    '''
    kit._render_event.wait(timeout)

def _m_beep(p):
    kit.beep(p['frequency'], p['duration'])

def _m_scroll(p):
    # runs synchronously on the calling thread's connection - fine since
    # scroll() blocks in pixelkit.py too. Call in a thread if you don't
    # want the socket held open for the whole scroll duration.
    kit.scroll(
        p.get('text', ''),
        color=tuple(p.get('color', (255, 255, 255))),
        background=tuple(p.get('background', (0, 0, 0))),
        interval=p.get('interval', 0.1),
    )

METHODS = {
    'hello':           _m_hello,
    'set-pixel':       _m_set_pixel,
    'set-pixel-hsv':   _m_set_pixel_hsv,
    'set-background':  _m_set_background,
    'clear':           _m_clear,
    'fill-rect':       _m_fill_rect,
    'draw-rect':       _m_draw_rect,
    'draw-line':       _m_draw_line,
    'draw-letter':     _m_draw_letter,
    'render':          _m_render,
    'render-frame':    _m_render_frame,
    'beep':            _m_beep,
    'scroll':          _m_scroll,
}


# --- connection handling ---------------------------------------------------

def _handle_client(conn, addr):
    f = conn.makefile('rwb')
    try:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                method = req.get('method')
                params = req.get('params', {}) or {}
                handler = METHODS.get(method)
                if handler is None:
                    raise ValueError('unknown method: %s' % method)
                result = handler(params)
                resp = {'id': req.get('id'), 'ok': True, 'result': result}
            except Exception as e:
                resp = {'id': req.get('id') if isinstance(req, dict) else None,
                         'ok': False, 'error': str(e)}
            f.write((json.dumps(resp) + '\n').encode('utf-8'))
            f.flush()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        conn.close()


def serve(port=PORT):
    print('[pixellink_agent] connecting to hardware...')
    kit.connect()
    print('[pixellink_agent] hardware ready, listening on %d' % port)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(8)

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == '__main__':
    serve()

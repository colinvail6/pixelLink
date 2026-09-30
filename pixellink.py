'''
pixellink.py - control Pixel Kits over the network from any computer.

Usage:
    import pixellink

    Kit1 = pixellink.connect("livingroom")
    Kit1.set_pixel(0, 0, (255, 0, 0))
    Kit1.render()

Chaining two or more kits into one wide virtual display, for text that
scrolls seamlessly across all of them:

    Kit1 = pixellink.connect("livingroom")
    Kit2 = pixellink.connect("desk")
    chain = pixellink.Chain([Kit1, Kit2])   # left to right
    chain.scroll("hello world")

"livingroom" is resolved as a hostname first (works with /etc/hosts,
mDNS "livingroom.local", or a real DNS entry). You can also connect
directly with an IP: pixellink.connect("192.168.1.44").

Each kit runs pixellink_agent.py, which wraps its native library
(pixelkit.py on BananaPi, or the CircuitPython port on the retail
ESP32 board) and exposes the same operations over the network - so
this client works identically against either hardware variant.

Pixel drawing (set_pixel, set_background, fill_rect, ...) only touches
a local in-memory buffer - nothing is sent over the network until you
call render(), which pushes the whole frame in a single round trip.
This keeps animation (scrolling, chained or not) smooth even though
every draw call is happening on a different computer than the LEDs.
'''

import json
import socket
import threading
import itertools
import time

import pixellink_font as font

DEFAULT_PORT = 7777


class PixelLinkError(Exception):
    pass


# --- colour helpers ----------------------------------------------------------

def hsv_to_rgb(h, s, v):
    if s == 0.0:
        x = int(v * 255)
        return (x, x, x)
    h_i = int(h * 6.0)
    f   = (h * 6.0) - h_i
    p   = v * (1.0 - s)
    q   = v * (1.0 - s * f)
    t   = v * (1.0 - s * (1.0 - f))
    h_i = h_i % 6
    if h_i == 0:   r, g, b = v, t, p
    elif h_i == 1: r, g, b = q, v, p
    elif h_i == 2: r, g, b = p, v, t
    elif h_i == 3: r, g, b = p, q, v
    elif h_i == 4: r, g, b = t, p, v
    else:          r, g, b = v, p, q
    return (int(r * 255), int(g * 255), int(b * 255))


# --- shared drawing surface ---------------------------------------------------

class _Canvas:
    '''
    Shared drawing logic for anything with a width, a height, and a way
    to set a pixel / push a frame. PixelLinkKit (one kit) and Chain
    (several kits linked side by side) both implement the small
    abstract surface below and get every other drawing method for free.
    '''

    width  = 0
    height = 0

    # -- abstract: implemented by PixelLinkKit and Chain --------------------

    def set_pixel(self, x, y, rgb=(0, 10, 0)):
        raise NotImplementedError

    def set_background(self, rgb=(255, 255, 0)):
        raise NotImplementedError

    def render(self):
        raise NotImplementedError

    # -- shared, built entirely on top of set_pixel/set_background ----------

    def clear(self):
        self.set_background((0, 0, 0))

    def set_pixel_hsv(self, x, y, hsv=(0, 1, 1)):
        self.set_pixel(x, y, hsv_to_rgb(*hsv))

    def fill_rect(self, x, y, w, h, rgb=(255, 255, 255)):
        for dy in range(h):
            for dx in range(w):
                self.set_pixel(x + dx, y + dy, rgb)

    def draw_rect(self, x, y, w, h, rgb=(255, 255, 255)):
        for dx in range(w):
            self.set_pixel(x + dx, y,         rgb)
            self.set_pixel(x + dx, y + h - 1, rgb)
        for dy in range(1, h - 1):
            self.set_pixel(x,         y + dy, rgb)
            self.set_pixel(x + w - 1, y + dy, rgb)

    def draw_line(self, x0, y0, x1, y1, rgb=(255, 255, 255)):
        dx = abs(x1 - x0); dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.set_pixel(x0, y0, rgb)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy: err -= dy; x0 += sx
            if e2 <  dx: err += dx; y0 += sy

    def draw_letter(self, x, y, char, rgb=(255, 255, 255)):
        glyph = font.get_glyph(str(char))
        if not glyph:
            return
        for gy, row in enumerate(glyph):
            for gx, on in enumerate(row):
                if on:
                    self.set_pixel(x + gx, y + gy, rgb)

    def scroll(self, text, color=(255, 255, 255), background=(0, 0, 0), interval=0.1):
        '''
        Scroll text right-to-left across the full width of this canvas.
        Works the same whether the canvas is one kit or a Chain of
        several - the caller never needs to know how many kits are
        behind it.
        '''
        row_y = max(0, (self.height - font.GLYPH_HEIGHT) // 2)

        # Lay every character out into one wide bitmap, padded with a
        # full canvas-width of blank columns on each side so the text
        # enters from, and exits off, the right/left edges cleanly.
        pad = self.width
        cols = []
        for ch in text:
            glyph = font.get_glyph(ch)
            if glyph is None:
                cols.extend([[0] * font.GLYPH_HEIGHT for _ in range(font.GLYPH_WIDTH)])
            else:
                for gx in range(font.GLYPH_WIDTH):
                    cols.append([glyph[gy][gx] for gy in range(font.GLYPH_HEIGHT)])
            cols.append([0] * font.GLYPH_HEIGHT)  # 1-column gap between letters

        blank_col = [0] * font.GLYPH_HEIGHT
        buffer = ([blank_col] * pad) + cols + ([blank_col] * pad)

        for offset in range(len(buffer) - self.width + 1):
            self.set_background(background)
            for x in range(self.width):
                col = buffer[offset + x]
                for gy in range(font.GLYPH_HEIGHT):
                    if col[gy]:
                        self.set_pixel(x, row_y + gy, color)
            self.render()
            time.sleep(interval)

        self.clear()
        self.render()


# --- single kit ----------------------------------------------------------------

class PixelLinkKit(_Canvas):
    '''A connection to a single remote Pixel Kit.'''

    def __init__(self, host, port=DEFAULT_PORT, timeout=5.0, read_timeout=20.0):
        self.host = host
        self.port = port
        # `timeout` only bounds the initial TCP handshake. Once connected,
        # switch to `read_timeout` for every request/response after that -
        # render() etc. need more headroom than a quick connection check,
        # especially over less predictable networks (phone WiFi, etc.)
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(read_timeout)
        self._file = self._sock.makefile('rwb')
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

        info = self._call('hello', {})
        self.variant     = info.get('variant', 'unknown')
        self.width       = info.get('width', 16)
        self.height      = info.get('height', 8)
        self.has_battery = info.get('has_battery', False)
        self.has_mic     = info.get('has_mic', False)

        self._pixels = [(0, 0, 0)] * (self.width * self.height)

    # -- transport ------------------------------------------------------------

    def _call(self, method, params):
        with self._lock:
            req_id = next(self._ids)
            msg = json.dumps({'id': req_id, 'method': method, 'params': params})
            self._file.write((msg + '\n').encode('utf-8'))
            self._file.flush()

            line = self._file.readline()
            if not line:
                raise PixelLinkError('connection to %s closed' % self.host)
            resp = json.loads(line)

        if not resp.get('ok', False):
            raise PixelLinkError(resp.get('error', 'unknown error'))
        return resp.get('result')

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self):
        return '<PixelLinkKit %s (%s) %dx%d>' % (
            self.host, self.variant, self.width, self.height)

    # -- _Canvas surface --------------------------------------------------------

    def set_pixel(self, x, y, rgb=(0, 10, 0)):
        if 0 <= x < self.width and 0 <= y < self.height:
            self._pixels[y * self.width + x] = tuple(rgb)

    def set_background(self, rgb=(255, 255, 0)):
        rgb = tuple(rgb)
        self._pixels = [rgb] * (self.width * self.height)

    def render(self):
        '''Push the whole current frame to the kit in a single round trip.'''
        self._call('render-frame', {'pixels': [list(c) for c in self._pixels]})

    # -- extras not covered by _Canvas -------------------------------------------

    def beep(self, frequency, duration):
        self._call('beep', {'frequency': frequency, 'duration': duration})


# --- chain of kits ---------------------------------------------------------------

class Chain(_Canvas):
    '''
    Link several kits side by side into one wide virtual display.

    kits must be given left-to-right and share the same height. Once
    linked, draw_* / scroll() / render() all operate on the combined
    canvas - a scrolling message flows off the right edge of one kit
    straight onto the left edge of the next.

        Kit1 = pixellink.connect("livingroom")
        Kit2 = pixellink.connect("desk")
        chain = pixellink.Chain([Kit1, Kit2])
        chain.scroll("hello world")
    '''

    def __init__(self, kits):
        if not kits:
            raise ValueError('Chain needs at least one kit')
        heights = {k.height for k in kits}
        if len(heights) > 1:
            raise ValueError('all kits in a Chain must share the same height, got %s'
                              % sorted(heights))

        self.kits   = list(kits)
        self.height = kits[0].height
        self.width  = sum(k.width for k in kits)

        # x-offset each kit starts at within the combined canvas
        self._offsets = []
        x = 0
        for k in self.kits:
            self._offsets.append(x)
            x += k.width

    def __repr__(self):
        return '<Chain %s = %dx%d>' % (
            '+'.join(k.host for k in self.kits), self.width, self.height)

    # -- _Canvas surface --------------------------------------------------------

    def set_pixel(self, x, y, rgb=(0, 10, 0)):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        # find which kit this column belongs to and translate to its
        # local coordinate space - purely local buffer math, no network
        for kit, offset in zip(self.kits, self._offsets):
            if offset <= x < offset + kit.width:
                kit.set_pixel(x - offset, y, rgb)
                return

    def set_background(self, rgb=(255, 255, 0)):
        for kit in self.kits:
            kit.set_background(rgb)

    def render(self):
        '''
        Push every kit's frame at once. Fired concurrently (one thread
        per kit) rather than one-after-another, so adjacent kits stay
        in sync during fast animation like scrolling text.
        '''
        errors = []

        def _render_one(kit):
            try:
                kit.render()
            except Exception as e:
                errors.append((kit, e))

        threads = [threading.Thread(target=_render_one, args=(k,)) for k in self.kits]
        for t in threads: t.start()
        for t in threads: t.join()

        if errors:
            kit, err = errors[0]
            raise PixelLinkError('render failed on %s: %s' % (kit.host, err))

    def close(self):
        for kit in self.kits:
            kit.close()


# --- simple broadcast group (same content on every kit, not linked) --------------

class Group:
    '''
    Drive several kits with the *same* content at once - e.g. two kits
    across a room both showing an identical animation. For treating
    kits as one wide combined display (extended scrolling), use Chain
    instead.
    '''

    def __init__(self, kits):
        self.kits = list(kits)

    def _broadcast(self, fn_name, *args, **kwargs):
        for k in self.kits:
            getattr(k, fn_name)(*args, **kwargs)

    def set_pixel(self, x, y, rgb=(0, 10, 0)):
        self._broadcast('set_pixel', x, y, rgb)

    def set_background(self, rgb=(255, 255, 0)):
        self._broadcast('set_background', rgb)

    def clear(self):
        self._broadcast('clear')

    def render(self):
        self._broadcast('render')


# --- module-level connect() -------------------------------------------------

def connect(name_or_ip, port=DEFAULT_PORT, timeout=5.0, read_timeout=20.0):
    '''
    Connect to a Pixel Kit by hostname, mDNS name, or IP address.

    pixellink.connect("livingroom")        # tries "livingroom", then "livingroom.local"
    pixellink.connect("192.168.1.44")      # direct IP
    pixellink.connect("192.168.1.44", read_timeout=30.0)  # more patience per request,
                                                            # useful on less reliable
                                                            # networks (e.g. phone WiFi)
    '''
    candidates = [name_or_ip]
    if not name_or_ip.endswith('.local') and not _looks_like_ip(name_or_ip):
        candidates.append(name_or_ip + '.local')

    last_err = None
    for host in candidates:
        try:
            return PixelLinkKit(host, port=port, timeout=timeout, read_timeout=read_timeout)
        except (socket.gaierror, socket.timeout, ConnectionRefusedError, OSError) as e:
            last_err = e
    raise PixelLinkError('could not connect to "%s": %s' % (name_or_ip, last_err))


def _looks_like_ip(s):
    parts = s.split('.')
    return len(parts) == 4 and all(p.isdigit() for p in parts)

# pixelLink

Library for linking Kano Pixel Kits (retail and Kickstarter versions) using Python.

`pixelLink` lets you control one or more Kano Pixel Kits directly from a
computer on your network — regardless of whether the kit is the original
Kickstarter hardware (BananaPi + ATSAMD21 MCU) or the retail ESP32 board.
Each kit runs a small agent that speaks a common protocol, so your code
never has to know which variant it's talking to.

```python
import pixellink

Kit1 = pixellink.connect("livingroom")
Kit1.set_pixel(0, 0, (255, 0, 0))
Kit1.render()
```

## How it works

```
 ┌─────────────┐        TCP / JSON        ┌──────────────────────┐
 │  your script │ ───────────────────────► │  pixellink_agent.py  │
 │  (pixellink) │ ◄─────────────────────── │   running ON the kit │
 └─────────────┘                           └──────────┬───────────┘
                                                        │
                                          pixelkit.py (BananaPi)
                                          or native CircuitPython
                                          API (ESP32 retail board)
```

- **`agent/pixellink_agent.py`** runs on the Pixel Kit itself. It wraps
  the kit's existing hardware library and exposes it over a simple
  newline-delimited JSON RPC protocol on port `7777`.
- **`pixellink.py`** runs on your computer. `pixellink.connect(name)`
  opens a socket to the agent, and every method on the returned object
  (`set_pixel`, `render`, `scroll`, `beep`, ...) sends one JSON request
  and waits for the reply — so it behaves like calling the kit's own
  library directly.
- **`pixellink.Group`** lets you drive several kits together — useful
  for synced effects, or as the basis for tiling multiple grids into
  one larger virtual canvas.

## Requirements

- Python 3.7+ on both the kit and the controlling computer
- Standard library only — no extra pip packages
- On the Kickstarter/BananaPi variant, the agent depends on the kit's
  existing `pixelkit.py` / `rpcclient.py` (from
  [PKLauncher](https://github.com/colinvail6/PKLauncher)) already being
  present on the device
- The ESP32/CircuitPython agent is not published yet — see
  [Roadmap](#roadmap)

## Installing

`pixellink.py` and `pixellink_font.py` live at the repo root, so a
script has to either sit right next to them or have the repo
installed as a package to find them. Installing is the one that also
makes editors like Thonny (or PyCharm, VS Code, etc.) recognize the
import and give you autocomplete, since it's then a real package on
the interpreter's path rather than a loose file:

```bash
cd pixelLink
pip install -e .
```

Run that with whatever interpreter your editor is pointed at (in
Thonny: Tools → Options → Interpreter, or Tools → Manage packages →
install from local path). After that, `import pixellink` works from
any script, anywhere - including the `examples/` folder.

If you'd rather not install anything, the alternative is keeping your
script in the same folder as `pixellink.py`/`pixellink_font.py`, or
adding the repo root to `sys.path` manually at the top of your script.

## Quick start

**On the Pixel Kit** (BananaPi/Kickstarter hardware):

```bash
# pixelkit.py and rpcclient.py must already be on the device
python3 agent/pixellink_agent.py
```

**On the Pixel Kit** (retail ESP32/CircuitPython hardware):

1. Copy `agent/esp32/code.py` to the root of the CIRCUITPY drive
2. Copy `agent/esp32/settings.toml.example` to `settings.toml` on the
   same drive and fill in your WiFi credentials
3. Reset the board - it connects to WiFi, advertises itself over mDNS
   (`pixelkit.local` by default, or set `PIXELLINK_HOSTNAME` in
   `settings.toml`), and listens for pixellink connections

This assumes the retail firmware already exposes a native `pixelkit`
module (the same one your CircuitPython apps `import pixelkit as
kit`) - the agent just wraps it, the same way the BananaPi agent
wraps `pixelkit.py`. Unlike the BananaPi agent, it handles one
pixellink connection at a time (CircuitPython has no threading).

**On your computer:**

```python
import pixellink

Kit1 = pixellink.connect("livingroom")   # hostname, "livingroom.local", or an IP
Kit1.set_background((0, 0, 0))
Kit1.set_pixel(3, 2, (0, 255, 0))
Kit1.render()
```

See `examples/basic_example.py` and `examples/group_example.py`.

### Chaining kits (extended scrolling)

Two or more kits can be linked side by side into one wide virtual
display. Drawing and scrolling then treat them as a single canvas —
text scrolls off the right edge of one kit straight onto the left
edge of the next:

```python
import pixellink

Kit1 = pixellink.connect("livingroom")   # physically on the left
Kit2 = pixellink.connect("desk")         # physically on the right

chain = pixellink.Chain([Kit1, Kit2])
chain.scroll("hello world")
```

`Chain` supports every drawing method (`set_pixel`, `fill_rect`,
`draw_letter`, `scroll`, ...) - internally it routes each pixel to the
correct kit's local buffer and renders every kit in parallel so they
stay in sync frame to frame. Kits in a chain must share the same
height; widths can differ. See `examples/chain_example.py`.

Two kits showing the *same* thing (not linked into one canvas) is
still `pixellink.Group` - see [API](#api) below for the distinction.

### BLE (no WiFi needed)

If a kit is running `agent/esp32_ble/code.py` instead of the WiFi
agent, connect to it over Bluetooth instead:

```bash
pip install bleak   # or: pip install pixellink[ble]
```

```python
import pixellink_ble

Kit1 = pixellink_ble.connect("PixelLinkKit-3F2A9C1B")  # exact name, or a unique prefix
Kit1.set_pixel(0, 0, (255, 0, 0))
Kit1.render()
```

`PixelLinkKitBLE` shares the same drawing API as the WiFi client (they
both build on the same internal `_Canvas` base), so it works with
`Chain`/`Group` too - a BLE kit and a WiFi kit can be `Chain`'d
together. This is a separate module (`pixellink_ble.py`, not bundled
into `pixellink.py`) so the core library keeps its zero-dependency,
standard-library-only footprint for anyone not using BLE. See
`examples/ble_example.py`.

Note: this has only been verified with hardware-in-the-loop on the
board side (Bluefruit Connect talking to `agent/esp32_ble/code.py`).
The `bleak`-based host client hasn't been tested against real
hardware yet - treat it as a first draft.

## API

`pixellink.connect(name_or_ip, port=7777)` → `PixelLinkKit`

`PixelLinkKit` methods (mirror the kit's native drawing API):

| Method | Description |
|---|---|
| `set_pixel(x, y, rgb)` | set a single pixel |
| `set_pixel_hsv(x, y, hsv)` | set a single pixel by HSV |
| `set_background(rgb)` | fill the whole grid |
| `clear()` | turn off all pixels |
| `fill_rect(x, y, w, h, rgb)` | filled rectangle |
| `draw_rect(x, y, w, h, rgb)` | rectangle outline |
| `draw_line(x0, y0, x1, y1, rgb)` | line |
| `draw_letter(x, y, char, rgb)` | draw one character |
| `scroll(text, color, background, interval)` | scroll text across the grid |
| `render()` | push the current frame to the hardware |
| `beep(frequency, duration)` | play a tone |

`PixelLinkKit` also exposes `.width`, `.height`, `.variant`,
`.has_battery`, `.has_mic`, populated from the agent's handshake.
All drawing methods write to a local buffer; nothing reaches the
kit until `render()` sends the whole frame in one round trip.

`pixellink.Chain(kits)` links kits **left to right into one wide
canvas** — same drawing API as `PixelLinkKit`, but `.width` is the
sum of all kits' widths, and `set_pixel`/`scroll`/etc. operate across
all of them as if it were one display. Use this for extended
scrolling text.

`pixellink.Group(kits)` broadcasts `set_pixel` / `set_background` /
`clear` / `render` to a list of `PixelLinkKit` objects at once - each
kit shows the *same* content, rather than being combined into one
canvas like `Chain`.

## Roadmap

- [ ] Validate `pixellink_ble.py` against real BLE hardware (untested so far)
- [ ] Bluetooth peripheral (bluezero/GATT-server) agent for the BananaPi variant
- [ ] mDNS auto-discovery (list kits on the network without knowing names)
- [ ] Tiled-canvas mode (treat N kits arranged in a grid as one large display)
- [ ] Simple web/blockly front end for coding kits visually

## Related

- [PKLauncher](https://github.com/colinvail6/PKLauncher) — the app
  launcher and hardware library (`pixelkit.py`) this project builds on

## License

MIT

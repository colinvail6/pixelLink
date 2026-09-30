'''
ble_example.py - connect to a Pixel Kit over BLE UART instead of WiFi.

Requires: pip install bleak   (or: pip install pixellink[ble])
Requires the kit to be running agent/esp32_ble/code.py (not the WiFi
agent/esp32/code.py - a board runs one or the other, not both).
'''

import pixellink_ble

# connects by advertised name (or a unique prefix of it) - see the
# board's serial console on boot for its exact "PixelLinkKit-XXXXXXXX" name
Kit1 = pixellink_ble.connect("PixelLinkKit-")
print(Kit1)

Kit1.set_pixel(0, 0, (255, 0, 0))
Kit1.render()

Kit1.close()

'''
basic_example.py - minimal pixelLink usage

Run pixellink_agent.py on the Pixel Kit first (see agent/README or the
main README "Quick start" section), then run this from any computer
on the same network.
'''

import pixellink

Kit1 = pixellink.connect("livingroom")   # hostname, mDNS name, or IP
print(Kit1)                              # e.g. <PixelLinkKit livingroom (bananapi) 16x8>

Kit1.set_pixel(0, 0, (255, 0, 0))
Kit1.render()

Kit1.close()

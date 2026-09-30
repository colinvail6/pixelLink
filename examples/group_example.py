'''
group_example.py - drive multiple linked Pixel Kits at once
'''

import pixellink

kit_living = pixellink.connect("livingroom")
kit_desk   = pixellink.connect("desk")

group = pixellink.Group([kit_living, kit_desk])

group.set_background((0, 0, 255))
group.render()

kit_living.close()
kit_desk.close()

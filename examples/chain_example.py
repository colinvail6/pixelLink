'''
chain_example.py - link two kits into one wide display and scroll
text seamlessly across both of them.

Physically, "livingroom" should be positioned to the left of "desk"
for this to look right (Chain treats kits left-to-right in the order
given).
'''

import pixellink

Kit1 = pixellink.connect("livingroom")
Kit2 = pixellink.connect("desk")

chain = pixellink.Chain([Kit1, Kit2])   # left to right
print(chain, "-", chain.width, "x", chain.height, "combined")

chain.scroll("hello world", color=(0, 255, 255), interval=0.08)

Kit1.close()
Kit2.close()

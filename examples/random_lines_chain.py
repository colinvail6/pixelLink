'''
random_lines_chain.py - draw random lines across a chain of kits.

Since Chain shares the exact same drawing API as a single kit, the
lines are drawn against the combined canvas - a line can start on one
kit and end on the other, crossing the seam seamlessly.
'''

import random
import time

import pixellink

Kit1 = pixellink.connect("livingroom")   # physically on the left
Kit2 = pixellink.connect("desk")         # physically on the right

chain = pixellink.Chain([Kit1, Kit2])
print(chain, "-", chain.width, "x", chain.height, "combined")


def random_point():
    return (random.randint(0, chain.width - 1), random.randint(0, chain.height - 1))


def random_color():
    return (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))


try:
    while True:
        chain.clear()
        for _ in range(4):                       # a handful of lines per frame
            x0, y0 = random_point()
            x1, y1 = random_point()
            chain.draw_line(x0, y0, x1, y1, random_color())
        chain.render()
        time.sleep(0.3)
except KeyboardInterrupt:
    pass
finally:
    chain.clear()
    chain.render()
    Kit1.close()
    Kit2.close()

from __future__ import print_function

import sys

import cairo
import texlib.wrap as texwrap

MARGIN = 10

def create_nodes(text, xadvance, debug=False):
    nodes = texwrap.ObjectList()
    nodes.debug = debug

    for ch in text:
        if ch in " ":
            nodes.append(texwrap.Glue(xadvance, xadvance / 2, xadvance / 2))
        elif ch == "\u00A0": # No-Break Space.
            nodes.append(texwrap.Penalty(0, texwrap.INFINITY))
            nodes.append(texwrap.Glue(xadvance, xadvance / 2, xadvance / 2))
        else:
            nodes.append(texwrap.Box(xadvance, ch))
    nodes.add_closing_penalty()

    return nodes

def draw_output(cr, nodes, breaks, lengths):
    ascent, descent, height, xadvance, yadvance = cr.font_extents()
    cr.set_source_rgb(0, 0, 0)

    line_start = 0
    line = 0
    for breakpoint in breaks[1:]:
        y = MARGIN + (ascent + descent) * (line + 1)
        x = MARGIN

        ratio = nodes.compute_adjustment_ratio(line_start, breakpoint, line, lengths)
        line += 1
        for i in range(line_start, breakpoint):
            box = nodes[i]
            if box.is_glue():
                width = box.compute_width(ratio)
                x += width
            elif box.is_box():
                cr.move_to(x, y)
                cr.show_text(box.character)
                x += box.width
            else:
                pass
        line_start = breakpoint + 1

def main(text, width, debug=False):
    lengths = [width]

    width = width + 2 * MARGIN
    height = 500 + 2 * MARGIN
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)

    cr = cairo.Context(surface)
    cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(20)
    cr.set_source_rgb(1, 1, 1)
    cr.rectangle(0, 0, width, height)
    cr.fill()

    ascent, descent, height, xadvance, yadvance = cr.font_extents()

    nodes = create_nodes(text, xadvance, debug)
    breaks = nodes.compute_breakpoints(lengths)
    draw_output(cr, nodes, breaks, lengths)

    surface.write_to_png("test.png")

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))

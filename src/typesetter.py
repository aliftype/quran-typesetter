from __future__ import print_function

import sys

import cairo
import texlib.wrap as texwrap

class Typesetter:
    MARGIN = 10

    def __init__(self, text, surface, width, height, debug=False):
        self.text = text
        self.width = width
        self.height = height
        self.debug = debug

        cr = self.cr = cairo.Context(surface)
        cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(20)

        self.ascent, self.descent, height, self.xadvance, yadvance = cr.font_extents()

    def output(self):
        self._create_nodes()
        self._compute_breaks()
        self._draw_output()
        self.cr.show_page()

    def _create_nodes(self):
        nodes = self.nodes = texwrap.ObjectList()
        nodes.debug = self.debug

        adv = self.xadvance

        for ch in self.text:
            if ch in " ":
                nodes.append(texwrap.Glue(adv, adv / 2, adv / 2))
            elif ch == "\u00A0": # No-Break Space.
                nodes.append(texwrap.Penalty(0, texwrap.INFINITY))
                nodes.append(texwrap.Glue(adv, adv / 2, adv / 2))
            else:
                nodes.append(texwrap.Box(adv, ch))
        nodes.add_closing_penalty()

    def _compute_breaks(self):
        lengths = [self.width]
        self.breaks = self.nodes.compute_breakpoints(lengths)

    def _draw_output(self):
        self.cr.set_source_rgb(0, 0, 0)

        lengths = [self.width]
        line_start = 0
        line = 0
        for breakpoint in self.breaks[1:]:
            y = self.MARGIN + (self.ascent + self.descent) * (line + 1)
            x = self.MARGIN

            ratio = self.nodes.compute_adjustment_ratio(line_start, breakpoint, line, lengths)
            line += 1
            for i in range(line_start, breakpoint):
                box = self.nodes[i]
                if box.is_glue():
                    width = box.compute_width(ratio)
                    x += width
                elif box.is_box():
                    self.cr.move_to(x, y)
                    self.cr.show_text(box.character)
                    x += box.width
                else:
                    pass
            line_start = breakpoint + 1

def main(text, width, debug, filename):
    surface = cairo.PDFSurface(filename, 1000, 1000)

    height = 1000
    typesetter = Typesetter(text, surface, width, height, debug)
    typesetter.output()

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])

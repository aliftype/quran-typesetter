from __future__ import print_function

import sys

import texlib.wrap as texwrap

def create_nodes(text, debug=False):
    nodes = texwrap.ObjectList()
    nodes.debug = debug

    for ch in text:
        if ch in " ":
            nodes.append(texwrap.Glue(2, 1, 1))
        elif ch == "\u00A0": # No-Break Space.
            nodes.append(texwrap.Penalty(0, texwrap.INFINITY))
            nodes.append(texwrap.Glue(2, 1, 1))
        else:
            nodes.append(texwrap.Box(1, ch))
    nodes.add_closing_penalty()

    return nodes

def draw_output(nodes, breaks, lengths):
    line_start = 0
    line = 0
    for breakpoint in breaks[1:]:
        ratio = nodes.compute_adjustment_ratio(line_start, breakpoint, line, lengths)
        line += 1
        for i in range(line_start, breakpoint):
            box = nodes[i]
            if box.is_glue():
                width = box.compute_width(ratio)
                print(width, end=',', file=sys.stderr)
                print(' ' * int(width), end='')
            elif box.is_box():
                print(box.character, end='')
            else:
                pass
        line_start = breakpoint + 1
        print(ratio, file=sys.stderr)
        print()

def main(text, width, debug=False):
    lengths = [width]

    nodes = create_nodes(text, debug)
    breaks = nodes.compute_breakpoints(lengths)

    print(breaks)
    draw_output(nodes, breaks, lengths)

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))

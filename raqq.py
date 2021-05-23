import logging
import math
import unicodedata

import harfbuzz as hb
import qahirah as qh

import linebreak

from number import format_number

ft = qh.get_ft_lib()


logging.basicConfig(format="%(asctime)s - %(message)s")
logger = logging.getLogger("typesetter")
logger.setLevel(logging.INFO)


DIGITS = ("٠", "١", "٢", "٣", "٤", "٥", "٦", "٧", "٨", "٩")
RIGH_JOINING = ("ا", "آ", "أ", "إ", "د", "ذ", "ر", "ز", "و", "ؤ")

GID_OFFSET = 0x10FFFF


def get_glyph(font, font_data, unicode, user_data):
    if unicode > GID_OFFSET:
        return unicode - GID_OFFSET
    return font.parent.get_nominal_glyph(unicode)


class Document:
    """Class representing the main document and holding document-wide settings
    and state."""

    def __init__(self, chapters, filename, debug):
        logger.info("Initializing the document: %s", filename)

        self.debug = debug

        # Settings
        # The defaults here roughly match “the 12-lines Mushaf”.
        self.body_font = "Raqq.otf"
        self.body_font_size = 125
        self.lines_per_page = 5
        self.leading = 102
        self.text_width = 717
        self.page_width = 1024
        self.page_height = 755
        # From top of page to first baseline.
        self.top_margin = 193

        self.text_start_pos = self.text_width + (self.page_width - self.text_width) / 2

        self.shaper = Shaper(self)

        self.surface = qh.PDFSurface.create(
            filename, (self.page_width, self.page_height)
        )
        cr = self.cr = qh.Context.create(self.surface)
        ft_face = ft.new_face(self.body_font)
        cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))
        cr.set_font_size(self.body_font_size)
        cr.set_source_colour(qh.Colour.grey(0))

        self.chapters = chapters

    def save(self):
        lines = self._create_lines()
        pages = self._create_pages(lines)

        logger.info("Drawing pages…")
        for page in pages:
            page.draw(self.cr)

        del self.cr
        del self.surface

    def _create_lines(self):
        """Processes each chapter and creates lines for the whole document."""

        logger.info("Breaking text into lines…")

        lines = []
        for chapter in self.chapters:
            lines.extend(self._process_chapter(chapter))

        return lines

    def _create_pages(self, lines):
        """Breaks the lines into pages"""

        logger.info("Breaking lines into pages…")

        npages = len(lines) // self.lines_per_page
        if len(lines) % self.lines_per_page:
            npages += 1

        pages = [Page(self, [], 1)]
        for i in range(npages):
            page = Page(self, [], len(pages) + 1)
            start = i * self.lines_per_page
            end = min((i + 1) * self.lines_per_page, len(lines))
            page.lines = lines[start:end]
            pages.append(page)

        return pages

    def _create_heading(self, chapter):
        boxes = self.shaper.shape_paragraph(chapter.get_heading_text())

        return Heading(self, boxes)

    def _process_chapter(self, chapter):
        """Shapes the text and breaks it into lines."""

        logger.info("Chapter %d…", chapter.number)

        lengths = [self.text_width]
        text = ""
        if chapter.opening:
            text = "بسمِ الله الرَحمنِ الرحيمِ؞ "
        nodes = self.shaper.shape_paragraph(text + chapter.text)
        breaks = nodes.compute_breakpoints(lengths, tolerance=4, looseness=10)
        assert breaks[-1] == len(nodes) - 1

        lines = [self._create_heading(chapter)]

        start = 0
        for i, breakpoint in enumerate(breaks[1:]):
            ratio = nodes.compute_adjustment_ratio(start, breakpoint, i, lengths)

            boxes = []
            for j in range(start, breakpoint):
                box = nodes[j]
                box.ratio = ratio
                boxes.append(box)

            lines.append(Line(self, boxes))

            start = breakpoint + 1

        return lines


class Chapter:
    """Class holding input text and metadata for a chapter."""

    def __init__(self, text, number, name, place, opening, verses):
        self.text = text
        self.number = number
        self.name = name
        self.place = place
        self.opening = opening
        self.verses = verses

    def get_heading_text(self):
        verses = format_number(self.verses)
        text = f"{self.name} {verses}"

        return text


class Shaper:
    """Class for turning text into boxes and glue."""

    def __init__(self, doc):
        self.doc = doc

        blob = hb.Blob.create_from_file(doc.body_font)
        self.face = hb.Face.create(blob, 0, True)
        self.font = self.make_font()

        self.buffer = hb.Buffer.create()

        self.minfont, self.minaxis = self.make_var_font("ASHR")
        self.maxfont, self.maxaxis = self.make_var_font("ASTR")

        self.reshape_font_funcs = hb.FontFuncs.create(True)
        self.reshape_font_funcs.set_nominal_glyph_func(get_glyph, None, None)

    def make_font(self):
        font = hb.Font.create(self.face)
        font.scale = (self.doc.body_font_size, self.doc.body_font_size)
        return font

    def make_var_font(self, tag):
        axis = self.face.ot_var_find_axis_info(hb.HARFBUZZ.TAG(tag))
        axis.tag = tag
        font = self.make_font()
        font.set_variations([hb.Variation.from_string(f"{tag}={axis.max_value}")])
        return font, axis

    def clear_buffer(self, direction=hb.HARFBUZZ.DIRECTION_RTL):
        buf = self.buffer

        buf.clear_contents()
        buf.direction = direction
        buf.script = hb.HARFBUZZ.SCRIPT_ARABIC
        buf.language = hb.Language.from_string("ar")

        return buf

    def shape(self, text, direction):
        buf = self.clear_buffer(direction)
        buf.add_str(text)
        hb.shape(self.font, buf)

        return buf

    def reshape(self, glyphs, variations):
        font = self.make_font()

        var = []
        for tag, value in variations.items():
            var.append(hb.Variation())
            var[-1].tag, var[-1].value = tag, value

        font.set_variations(var)
        font = font.create_sub_font()
        font.set_funcs(self.reshape_font_funcs, None, None)

        buf = self.clear_buffer()
        codepoints = [g.index + GID_OFFSET for g in reversed(glyphs)]
        buf.add_codepoints(codepoints, len(codepoints), 0, len(codepoints))
        hb.shape(font, buf)

        return buf.get_glyphs()[0]

    @staticmethod
    def next_is_nonjoining(text, infos, index):
        if index < len(infos):
            cluster = infos[index].cluster
            category = unicodedata.category(text[cluster])
            return category[0] != "L"
        return True

    def shape_verse(self, verse, mark=None):
        """
        Shapes a single verse and returns the corresponding nodes.
        """

        buf = self.shape(verse, hb.HARFBUZZ.DIRECTION_RTL)

        nodes = []
        infos = buf.glyph_infos
        positions = buf.glyph_positions
        flip = qh.Vector(1, -1)
        i = len(infos) - 1
        while i >= 0:
            # Find all indices with same cluster
            j = i
            while j >= 0 and infos[i].cluster == infos[j].cluster:
                j -= 1

            # Collect all glyphs in this cluster, iterating backwards to get
            # glyphs in the visual order.
            pos = qh.Vector(0, 0)
            glyphs = []
            for k in reversed(range(i, j, -1)):
                glyphs.append(
                    qh.Glyph(infos[k].codepoint, pos + flip * positions[k].offset)
                )
                pos += flip * positions[k].advance

            # The chars in this cluster
            chars = verse[infos[i].cluster : infos[j].cluster]

            # We skip space since the font kerns with it and we will turn these
            # kerns into glue below.
            if chars != " ":
                # Find the last non-combining mark char in the string, to check
                # for joining behaviour.
                for ch in chars:
                    if not unicodedata.combining(ch):
                        base = ch

                adv = self.font.get_glyph_h_advance(glyphs[-1].index)
                minadv = self.minfont.get_glyph_h_advance(glyphs[-1].index)
                maxadv = self.maxfont.get_glyph_h_advance(glyphs[-1].index)

                shrink = adv - minadv
                stretch = maxadv - adv

                if base in RIGH_JOINING or self.next_is_nonjoining(verse, infos, j):
                    # Get the difference between the original advance width and
                    # the advance width after OTL.
                    kern = positions[k].advance - qh.Vector(adv, 0)

                    # Re-adjust glyph positions.
                    glyphs = [qh.Glyph(g.index, g.pos - kern) for g in glyphs]
                    nodes.append(Box(self.doc, chars, glyphs, adv, stretch, shrink))

                    # Add glue with the kerning amount with minimal stretch and shrink.
                    nodes.append(Glue(self.doc, kern.x, kern.x / 8.5, kern.x / 8.5))
                else:
                    nodes.append(Box(self.doc, chars, glyphs, pos.x, stretch, shrink))
            elif pos.x != 0:
                # If space is not zero-width, add glue for it.
                nodes.append(Glue(self.doc, pos.x, pos.x / 8.5, pos.x / 8.5))

            i = j

        if mark:
            buf = self.shape(mark, hb.HARFBUZZ.DIRECTION_LTR)
            glyphs, pos = buf.get_glyphs()
            nodes.append(Box(self.doc, mark, glyphs, pos.x))

        return nodes

    def shape_paragraph(self, text):
        """
        Converts the text to a list of boxes and glues that the line breaker
        will work on.
        """
        nodes = linebreak.NodeList()

        # Split the text into verses, using aya mark as seperator.
        verse = ""
        text = text.strip()
        textlen = len(text)
        i = 0
        while i < textlen:
            ch = text[i]
            if ch == "\u06DD":
                mark = ch
                i += 1
                while i < textlen and text[i] in DIGITS:
                    mark += text[i]
                    i += 1
                nodes.extend(self.shape_verse(verse, mark))
                verse = ""
            else:
                verse += ch
                i += 1

        nodes.extend(self.shape_verse(verse))
        nodes.add_closing_penalty()

        return nodes


class Page:
    """Class representing a page of text."""

    def __init__(self, doc, lines, number):
        self.doc = doc
        self.lines = lines
        self.number = number

    def draw(self, cr):
        logger.info("Page %d…", self.number)

        shaper = self.doc.shaper
        self.cr = cr

        if not self.lines:
            logger.debug("Leaving empty page blank")
            cr.show_page()
            return

        lines = self.lines
        pos = qh.Vector(0, self.doc.top_margin)
        for i, line in enumerate(lines):
            pos.x = self.doc.text_start_pos
            line.draw(cr, pos)
            pos.y += line.height

        cr.show_page()


class Glue(linebreak.Glue):
    def __init__(self, doc, width, stretch, shrink):
        super().__init__(width=width, stretch=stretch, shrink=shrink)
        self.doc = doc

    def draw(self, cr, pos):
        width = self.compute_width()
        x, y = pos.x - width, pos.y

        if self.doc.debug and width != self.width:
            cr.save()
            if self.ratio > 0:
                cr.set_source_colour((0, 1, 0, 0.2))
            else:
                cr.set_source_colour((0, 0, 1, 0.2))
            cr.rectangle(qh.Rect(x, y, width, -5))
            cr.fill()
            cr.restore()

        return x


class Box(linebreak.Box):
    def __init__(self, doc, text, glyphs, width, stretch=0, shrink=0):
        super().__init__(width=width, stretch=stretch, shrink=shrink)
        self.doc = doc
        self.text = text
        self.glyphs = glyphs

    def draw(self, cr, pos):
        cr.save()
        glyphs = self.glyphs
        shaper = self.doc.shaper
        face = shaper.font.face

        width = self.compute_width()
        x, y = pos.x - width, pos.y
        cr.translate((x, y))

        if width != self.width:
            axis = shaper.maxaxis if self.ratio > 0 else shaper.minaxis
            variations = {
                axis.tag: abs(self.ratio) * (axis.max_value - axis.default_value)
            }

            glyphs = shaper.reshape(glyphs, variations)

            ft_face = ft.new_face(self.doc.body_font)
            axes = ft_face.mm_var["axis"]
            coords = []
            for axis in axes:
                tag = axis["tag"]
                if tag in variations:
                    coords.append(variations[tag])
                else:
                    coords.append(axis["default"])

            ft_face.set_var_design_coordinates(coords)
            cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))

        colors = face.ot_colour_palette_get_colours(0)

        for glyph in glyphs:
            layers = face.ot_colour_glyph_get_layers(glyph.index)
            if layers:
                for layer in layers:
                    color = colors[layer.colour_index]
                    color = [
                        hb.HARFBUZZ.colour_get_red(color),
                        hb.HARFBUZZ.colour_get_green(color),
                        hb.HARFBUZZ.colour_get_blue(color),
                        hb.HARFBUZZ.colour_get_alpha(color),
                    ]
                    color = [c / 255 for c in color]
                    lglyph = qh.Glyph(layer.glyph, glyph.pos)
                    cr.save()
                    cr.set_source_colour(color)
                    cr.show_glyphs([lglyph])
                    cr.restore()
            else:
                cr.show_glyphs([glyph])
        cr.restore()

        if self.doc.debug and width != self.width:
            cr.save()
            if self.ratio > 0:
                cr.set_source_colour((0, 1, 0, 0.2))
            else:
                cr.set_source_colour((0, 0, 1, 0.2))
            cr.rectangle(qh.Rect(x, y - self.doc.leading + 30, width, 5))
            cr.fill()
            cr.restore()

        return x


class Line:
    """Class representing a line of text."""

    def __init__(self, doc, boxes):
        self.doc = doc
        self.height = doc.leading
        self.boxes = boxes

    def draw(self, cr, pos):
        self.strip()

        for box in self.boxes:
            pos.x = box.draw(cr, pos)

    def strip(self):
        while self.boxes and not self.boxes[-1].is_box:
            self.boxes.pop()


class Heading(Line):
    """Class representing a chapter heading."""

    def __init__(self, doc, boxes):
        super().__init__(doc, boxes)

    def draw(self, cr, pos):
        cr.save()
        cr.set_source_colour((0.83, 0.68, 0.21))  # XXX
        super().draw(cr, pos)
        cr.restore()


def read_data(datadir):
    path = os.path.join(datadir, "meta.txt")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as textfile:
            metadata = {}
            lines = [l.strip().split("\t") for l in textfile.readlines()]
            for num, line in enumerate(lines):
                num += 1
                metadata[num] = [line[0], line[1], True]
                if len(line) >= 3:
                    metadata[num][2] = int(line[2])
    else:
        logger.error("File not found: %s", path)
        return

    chapters = {}
    for i in range(1, 115):
        path = os.path.join(datadir, "%03d.txt" % i)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as textfile:
                lines = [l.strip("\n") for l in textfile.readlines()]
                chapter = Chapter(" ".join(lines), i, *metadata[i], len(lines))
                chapters[i] = chapter
        else:
            pass

    return chapters


def main(chapters, filename, debug):
    document = Document(chapters, filename, debug)
    document.save()


if __name__ == "__main__":
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(description="Quran Typesetter.")
    parser.add_argument(
        "datadir", metavar="DATADIR", help="Directory containing input files to process"
    )
    parser.add_argument("outfile", metavar="OUTFILE", help="Output file")
    parser.add_argument(
        "--chapters",
        "-c",
        metavar="N",
        nargs="*",
        type=int,
        choices=range(1, 115),
        default=range(1, 115),
        help="Which chapters to process (Default: all)",
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help="Draw some debugging aids"
    )
    parser.add_argument(
        "--quite", "-q", action="store_true", help="Don’t print normal messages"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print verbose messages"
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    if args.quite:
        logger.setLevel(logging.ERROR)

    all_chapters = read_data(args.datadir)
    if all_chapters is None:
        sys.exit(1)

    chapters = []
    for i in args.chapters:
        chapters.append(all_chapters[i])

    main(chapters, args.outfile, args.debug)

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


class Document:
    """Class representing the main document and holding document-wide settings
       and state."""

    def __init__(self, chapters, filename, debug):
        logger.info("Initializing the document: %s", filename)

        self.debug = debug

        # Settings
        # The defaults here roughly match “the 12-lines Mushaf”.
        self.body_font        = "Raqq"
        self.body_font_size   = 77
        self.lines_per_page   = 5
        self.leading          = 102
        self.text_width       = 717
        self.page_width       = 1024
        self.page_height      = 755
        # From top of page to first baseline.
        self.top_margin       = 193

        self.text_start_pos = self.text_width + (self.page_width - self.text_width) / 2

        # Cache for shaped words.
        self.shaper = Shaper(self)

        self.surface = qh.PDFSurface.create(filename, (self.page_width,
                                                       self.page_height))
        # Create a new FreeType face for Cairo, as sometimes Cairo mangles the
        # char size, breaking HarfBuzz positions when it uses the same face.
        ft_face = ft.find_face(self.body_font)
        cr = self.cr = qh.Context.create(self.surface)
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
        #assert breaks[-1] == len(nodes) - 1

        lines = [self._create_heading(chapter)]

        start = 0
        for i, breakpoint in enumerate(breaks[1:]):
            ratio = nodes.compute_adjustment_ratio(start, breakpoint, i,
                                                   lengths)

            boxes = []
            for j in range(start, breakpoint):
                box = nodes[j]
                if box.is_glue():
                    box.width = box.compute_width(ratio)
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
        ft_face = ft.find_face(doc.body_font)
        ft_face.set_char_size(size=doc.body_font_size, resolution=qh.base_dpi)
        self.font = hb.Font.ft_create(ft_face)
        self.buffer = hb.Buffer.create()

        # Get the natural space width
        self.space = self.shape_word(" ").width

    def shape_word(self, text):
        """
        Shapes a single word and returns the corresponding box. To speed things
        a bit, we cache the shaped words. We assume all our text is in Arabic
        script and language. The direction is almost always right-to-left,
        (we are cheating a bit to avoid doing proper bidirectional text as
        it is largely superfluous for us here).
        """

        self.buffer.clear_contents()
        self.buffer.add_str(text)
        # Everything is RTL except aya numbers and other digits-only words.
        if text[0] == "\u06DD":
            self.buffer.direction = hb.HARFBUZZ.DIRECTION_LTR
        else:
            self.buffer.direction = hb.HARFBUZZ.DIRECTION_RTL
        self.buffer.script = hb.HARFBUZZ.SCRIPT_ARABIC
        self.buffer.language = hb.Language.from_string("ar")

        hb.shape(self.font, self.buffer)

        box = Box(self.doc, Word(text, self.buffer))

        return box

    @staticmethod
    def next_is_nonjoining(text, infos, index):
        if index < len(infos):
            cluster = infos[index].cluster
            category = unicodedata.category(text[cluster])
            return category[0] != "L"
        return True

    def shape_verse(self, verse):
        """
        Shapes a single verse and returns the corresponding nodes.
        """

        buf = self.buffer

        buf.clear_contents()
        buf.add_str(verse)
        buf.direction = hb.HARFBUZZ.DIRECTION_RTL
        buf.script = hb.HARFBUZZ.SCRIPT_ARABIC
        buf.language = hb.Language.from_string("ar")

        hb.shape(self.font, self.buffer)
        infos = buf.glyph_infos
        positions = buf.glyph_positions
        flip = qh.Vector(1, -1)
        i = len(infos) - 1
        nodes = []
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
                glyphs.append(qh.Glyph(infos[k].codepoint, pos + flip * positions[k].offset))
                pos += flip * positions[k].advance

            # The chars in this cluster
            chars = verse[infos[i].cluster:infos[j].cluster]

            # We skip space since the font kerns with it and we will turn these
            # kerns into glue below.
            if chars != " ":
                # Find the last non-combining mark char in the string, to check
                # for joining behaviour.
                for ch in chars:
                    if not unicodedata.combining(ch):
                        base = ch

                if base in RIGH_JOINING or self.next_is_nonjoining(verse, infos, j):
                    # Get the difference between the original advance width and
                    # the advance width after OTL.
                    adv = self.font.get_glyph_h_advance(glyphs[-1].index)
                    kern = positions[k].advance - qh.Vector(adv, 0)

                    # Re-adjust glyph positions.
                    glyphs = [qh.Glyph(g.index, g.pos - kern) for g in glyphs]
                    nodes.append(Box(self.doc, Cluster(chars, glyphs, adv)))

                    # Add glue with the kerning amount with minimal stretch and shrink.
                    nodes.append(Glue(self.doc, kern.x, kern.x / 10, kern.x / 10))
                else:
                    nodes.append(Box(self.doc, Cluster(chars, glyphs, pos.x)))

            i = j

        return nodes

    def shape_paragraph(self, text):
        """
        Converts the text to a list of boxes and glues that the line breaker
        will work on. We basically split text into words and shape each word
        separately then put it into a box. We don’t try to preserve the
        context when shaping the words, as we know that our font does not
        do anything special around spaces, which in turn allows us to cache
        the shaped words.
        """
        nodes = linebreak.NodeList()

        space = self.space

        # Split the text into words, treating space, newline and no-break space
        # as word separators.
        verse = ""
        text = text.strip()
        textlen = len(text)
        i = 0
        while i < textlen:
            ch = text[i]
            if ch == "\u06DD":
                nodes.extend(self.shape_verse(verse))
                verse = ""
                mark = ch
                i += 1
                while i < textlen and text[i] in DIGITS:
                    mark += text[i]
                    i += 1
                nodes.append(self.shape_word(mark))
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

        self.strip()

        lines = self.lines
        pos = qh.Vector(0, self.doc.top_margin)
        for i, line in enumerate(lines):
            pos.x = self.doc.text_start_pos
            line.draw(cr, pos)
            pos.y += line.height

        cr.show_page()

    def strip(self):
        while not self.lines[-1].is_box():
            self.lines.pop()

class Word:
    """Class representing a shaped word."""

    def __init__(self, text, buf):
        self.text = text

        glyphs, pos = buf.get_glyphs()
        self.glyphs = glyphs
        self.width = pos.x
        self.clusters = [(len(text), len(glyphs))]


class Cluster:
    """Class representing a shaped cluster."""

    def __init__(self, text, glyphs, width):
        self.text = text
        self.glyphs = glyphs
        self.width = width
        self.clusters = [(len(text), len(glyphs))]


class LineList(linebreak.NodeList):

    def __init__(self, doc):
        super().__init__()
        self.doc = doc

    def compute_breakpoints(self, line_lengths):
        # Copied from compute_breakpoints() since compute_adjustment_ratio()
        # needs them.
        self.sum_width = {}
        self.sum_shrink = {}
        self.sum_stretch = {}
        width_sum = shrink_sum = stretch_sum = 0
        for i, node in enumerate(self):
            self.sum_width[i] = width_sum
            self.sum_shrink[i] = shrink_sum
            self.sum_stretch[i] = stretch_sum

            width_sum += node.height
            shrink_sum += node.shrink
            stretch_sum += node.stretch

        # Calculate line breaks.
        # XXX: This seems rather hackish, clean it up!
        breaks = [0]
        height = 0
        last = 0
        i = 0
        while i < len(self):
            line = len(breaks)
            length = line_lengths[line if line < len(line_lengths) else -1]

            node = self[i]
            if node.is_box() or node.is_glue():
                height += node.height

            if not node.is_box():
                if height > length:
                    breaks.append(last)
                    height = 0
                    i = last
                elif height == length:
                    breaks.append(i)
                    height = 0
                else:
                    last = i
            i += 1

        if breaks[-1] != len(self) - 1:
            breaks.append(len(self) - 1)

        # Check that we are not overflowing the page, i.e. we don’t have more
        # lines per page (plus intervening glue) than we should.
        last = 0
        for i in breaks[1:]:
            assert i - last <= self.doc.lines_per_page * 2, (i, i - last)
            last = i

        return breaks


class Glue(linebreak.Glue):
    """Wrapper around linebreak.Glue to hold our common API."""

    def __init__(self, doc, width, stretch, shrink):
        super().__init__(width, stretch, shrink)
        self.doc = doc

    def draw(self, cr, pos):
        if self.doc.debug:
            width = self.width
            cr.save()
            if width >= 0:
                cr.set_source_colour((0, 1, 0, 0.2))
            else:
                cr.set_source_colour((1, 0, 0, 0.2))
            cr.rectangle(qh.Rect(pos.x, pos.y, width, -self.doc.leading))
            cr.fill()
            cr.restore()


class Penalty(linebreak.Penalty):
    """Wrapper around linebreak.Penalty to hold our common API."""

    def __init__(self, doc, width, penalty, flagged=0):
        super().__init__(width, penalty, flagged)
        self.doc = doc

    def draw(self, cr, pos):
        pass


class Box(linebreak.Box):
    """Class representing a word."""

    def __init__(self, doc, word):
        super().__init__(word.width, word)
        self.doc = doc

    def draw(self, cr, pos):
        cr.save()
        cr.translate(pos)
        word = self.data
        cr.show_text_glyphs(word.text, word.glyphs, word.clusters, 0)
        cr.restore()
        if self.doc.debug:
            width = self.width
            cr.save()
            cr.set_line_width(.5)
            cr.set_source_colour((0, 0, 1, 0.2))
            cr.rectangle(qh.Rect(pos.x, pos.y, width, -self.doc.leading))
            cr.stroke()
            cr.restore()


class Line(linebreak.Box):
    """Class representing a line of text."""

    def __init__(self, doc, boxes):
        super().__init__(doc.leading)
        self.doc = doc
        self.height = self.width
        self.boxes = boxes

    def draw(self, cr, pos):
        self.strip()

        for box in self.boxes:
            # We start drawing from the right edge of the text block,
            # and move to the left, thus the subtraction instead of
            # addition below.
            pos.x -= box.width
            box.draw(cr, pos)

    def strip(self):
        while self.boxes and not self.boxes[-1].is_box():
            self.boxes.pop()


class Heading(Line):
    """Class representing a chapter heading."""

    def __init__(self, doc, boxes):
        super().__init__(doc, boxes)
        self.height = doc.leading

    def draw(self, cr, pos):
        cr.save()
        cr.set_source_colour((0.83, 0.68, 0.21)) # XXX
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
    parser.add_argument("datadir", metavar="DATADIR",
            help="Directory containing input files to process")
    parser.add_argument("outfile", metavar="OUTFILE",
            help="Output file")
    parser.add_argument("--chapters", "-c", metavar="N", nargs="*", type=int,
            choices=range(1, 115), default=range(1, 115),
            help="Which chapters to process (Default: all)")
    parser.add_argument("--debug", "-d", action="store_true",
            help="Draw some debugging aids")
    parser.add_argument("--quite", "-q", action="store_true",
            help="Don’t print normal messages")
    parser.add_argument("--verbose", "-v", action="store_true",
            help="Print verbose messages")

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

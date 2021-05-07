import logging
import math
import unicodedata

import harfbuzz as hb
import qahirah as qh

import linebreak

ft = qh.get_ft_lib()

logging.basicConfig(format="%(asctime)s - %(message)s")
logger = logging.getLogger("typesetter")
logger.setLevel(logging.INFO)


class Document:
    """Class representing the main document and holding document-wide settings
       and state."""

    def __init__(self, chapters, filename):
        logger.info("Initializing the document: %s", filename)

        # Settungs
        # The defaults here roughly match “the 12-lines Mushaf”.
        self.body_font        = "Amiri Quran"
        self.body_font_size   = 11.5
        self.lines_per_page   = 12
        self.leading          = 29  # ~0.4in
        self.text_width       = 205 # ~2.84in
        self.page_width       = 396 # 5.5in
        self.page_height      = 540 # 7.5in
        # From top of page to first baseline.
        self.top_margin       = 105 # ~1.46

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

        lines = LineList(self)
        for chapter in self.chapters:
            lines.extend(self._process_chapter(chapter))

        return lines

    def _create_pages(self, lines):
        """Breaks the lines into pages"""

        logger.info("Breaking lines into pages…")

        pages = [Page(self, [], 1)]
        lengths = [self.leading * self.lines_per_page]
        breaks = lines.compute_breakpoints(lengths)
        assert breaks[-1] == len(lines) - 1

        start = 0
        for i, breakpoint in enumerate(breaks[1:]):
            ratio = lines.compute_adjustment_ratio(start, breakpoint, i,
                                                   lengths)

            page = Page(self, [], len(pages) + 1)
            for j in range(start, breakpoint):
                line = lines[j]
                if line.is_glue():
                    line.height = line.compute_width(ratio)
                page.lines.append(line)

            pages.append(page)
            start = breakpoint + 1

        return pages

    def _create_heading(self, chapter):
        lines = []
        for text in chapter.get_heading_text():
            boxes = self.shaper.shape_paragraph(text)
            lines.append(Line(self, boxes))

        return Heading(self, lines)

    def _process_chapter(self, chapter):
        """Shapes the text and breaks it into lines."""

        logger.info("Chapter %d…", chapter.number)

        lengths = [self.text_width]
        nodes = self.shaper.shape_paragraph(chapter.text)
        breaks = nodes.compute_breakpoints(lengths, tolerance=4, looseness=10)
        assert breaks[-1] == len(nodes) - 1

        lines = [self._create_heading(chapter)]
        if chapter.opening:
            box = self.shaper.shape_word("\uFDFD")
            lines.append(Line(self, [box]))

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
            lines.append(LineGlue(self))

            start = breakpoint + 1

        # Allow stretching the glue between chapters.
        lines[-1].stretch = self.leading

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
        text = []
        verses = format_number(self.verses)
        text.append(" سورة %s %s" % (self.name, self.place))
        text.append("و آياتها %s" % verses)

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

    def shape_word(self, word):
        """
        Shapes a single word and returns the corresponding box. To speed things
        a bit, we cache the shaped words. We assume all our text is in Arabic
        script and language. The direction is almost always right-to-left,
        (we are cheating a bit to avoid doing proper bidirectional text as
        it is largely superfluous for us here).
        """

        assert word

        text = word

        self.buffer.clear_contents()
        self.buffer.add_str(text)
        # Everything is RTL except aya numbers and other digits-only words.
        if text[0] in ("\u06DD", "(") or text.isdigit():
            self.buffer.direction = hb.HARFBUZZ.DIRECTION_LTR
        else:
            self.buffer.direction = hb.HARFBUZZ.DIRECTION_RTL
        self.buffer.script = hb.HARFBUZZ.SCRIPT_ARABIC
        self.buffer.language = hb.Language.from_string("ar")
        self.buffer.cluster_level = hb.HARFBUZZ.BUFFER_CLUSTER_LEVEL_MONOTONE_CHARACTERS

        hb.shape(self.font, self.buffer)

        box = Box(self.doc, Word(text, self.buffer))

        return box

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
        word = ""
        text = text.strip()
        textlen = len(text)
        for i, ch in enumerate(text):
            if ch == "\u00A0" and unicodedata.combining(text[i + 1] if i < textlen else ""):
                word += ch
            elif ch in (" ", "\u00A0"):
                nodes.append(self.shape_word(word))

                # Prohibit line breaking at no-break space.
                if ch == "\u00A0":
                    nodes.append(Penalty(self.doc, 0, linebreak.INFINITY))

                nodes.append(Glue(self.doc, space, space/2, space/1.5))
                word = ""
            else:
                word += ch
        nodes.append(self.shape_word(word)) # last word

        nodes.add_closing_penalty()

        return nodes


def format_number(number):
    """Format number to Arabic-Indic digits."""

    number = int(number)
    return "".join([chr(ord(c) + 0x0630) for c in str(number)])


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
        text_width = self.doc.text_width
        for i, line in enumerate(lines):
            pos.x = self.doc.text_start_pos
            line.draw(cr, pos, text_width)
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

        if False:
            # Do clusters per glyph/charcter, disabled for now as it does not
            # seem to improve things that much.
            self.backward = hb.HARFBUZZ.DIRECTION_IS_BACKWARD(buf.direction)
            infos = buf.glyph_infos
            if self.backward:
                infos = infos[::-1]

            clusters = []
            i = 0
            while i < len(infos):
                info = infos[i]

                n_glyphs = 1
                i += 1
                while (i < len(infos)) and (infos[i].cluster == info.cluster):
                    i += 1
                    n_glyphs += 1

                if i < len(infos):
                    next_cluster = infos[i].cluster
                else:
                    next_cluster = len(text)
                n_chars = next_cluster - info.cluster

                clusters.append((n_chars, n_glyphs))
            self.clusters = clusters
        else:
            self.backward = False
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

    def draw(self, cr, pos, text_width=0):
        pass


class Penalty(linebreak.Penalty):
    """Wrapper around linebreak.Penalty to hold our common API."""

    def __init__(self, doc, width, penalty, flagged=0):
        super().__init__(width, penalty, flagged)
        self.doc = doc

    def draw(self, cr, pos, text_width=0):
        pass


class Box(linebreak.Box):
    """Class representing a word."""

    def __init__(self, doc, word):
        super().__init__(word.width, word)
        self.doc = doc

    def draw(self, cr, pos, text_width=0):
        cr.save()
        cr.translate(pos)
        word = self.data
        if word.backward:
            flags = qh.CAIRO.TEXT_CLUSTER_FLAG_BACKWARD
        else:
            flags = 0
        cr.show_text_glyphs(word.text, word.glyphs, word.clusters, flags)
        cr.restore()


class LineGlue(Glue):
    def __init__(self, doc, height=0, stretch=0, shrink=0):
        super().__init__(doc, height, stretch, shrink)
        self.height = self.width


class Line(linebreak.Box):
    """Class representing a line of text."""

    def __init__(self, doc, boxes):
        super().__init__(doc.leading)
        self.doc = doc
        self.height = self.width
        self.boxes = boxes

    def draw(self, cr, pos, text_width):
        self.strip()
        width = sum([box.width for box in self.boxes])
        # Center lines not equal to text width.
        if not math.isclose(width, text_width):
            pos.x -= (text_width - width)/2

        for box in self.boxes:
            # We start drawing from the right edge of the text block,
            # and move to the left, thus the subtraction instead of
            # addition below.
            pos.x -= box.width
            box.draw(cr, pos)

    def strip(self):
        while not self.boxes[-1].is_box():
            self.boxes.pop()


class Heading(Line):
    """Class representing a chapter heading."""

    def __init__(self, doc, lines):
        super().__init__(doc, lines)
        self.height = doc.leading * 1.8

    def draw(self, cr, pos, width):
        offset = self.doc.leading/2
        height = self.height - offset

        linepos = qh.Vector(pos.x, pos.y)
        for line in self.boxes:
            line.draw(cr, linepos, width)
            linepos.x = pos.x
            linepos.y += line.height - offset/1.2

        cr.save()
        cr.set_line_width(.5)
        cr.move_to((pos.x, pos.y - offset))
        cr.rectangle(qh.Rect(pos.x - width, pos.y - offset, width, height))
        cr.stroke()
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

    chapters = []
    for i in range(1, 115):
        path = os.path.join(datadir, "%03d.txt" % i)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as textfile:
                lines = [l.strip("\n") for l in textfile.readlines()]
                chapter = Chapter(" ".join(lines), i, *metadata[i], len(lines))
                chapters.append(chapter)
        else:
            logger.error("File not found: %s", path)
            return

    return chapters

def main(chapters, filename):
    document = Document(chapters, filename)
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
        chapters.append(all_chapters[i - 1])

    main(chapters, args.outfile)

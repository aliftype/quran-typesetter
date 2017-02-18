import logging
import math

import harfbuzz as hb
import qahirah as qh
import texlib.wrap as texwrap

ft = qh.get_ft_lib()

logging.basicConfig(format="%(asctime)s - %(message)s")
logger = logging.getLogger("typesetter")
logger.setLevel(logging.INFO)


class Settings:
    """Class holding document wide settings."""

    def __init__(self):
        # The defaults here roughly match “the 12-lines Mushaf”.
        self.body_font        = "Amiri Quran"
        self.body_font_size   = 10
        self.lines_per_page   = 12
        self.leading          = 29  # ~0.4in
        self.text_widths      = [205] # ~2.84in
        self.page_width       = 396 # 5.5in
        self.page_height      = 540 # 7.5in
        # From top of page to first baseline.
        self.top_margin       = 105 # ~1.46
        self.outer_margin     = 100 # ~1.4in
        self.page_number_ypos = 460 # ~6.4in

    def get_text_width(self, line):
        if line >= len(self.text_widths):
            line = -1
        return self.text_widths[line]

    def get_page_number_pos(self, page, width):
        pos = qh.Vector(0, self.page_number_ypos)

        # Center the number relative to the text box.
        line = self.lines_per_page - 1
        text_width = self.get_text_width(line)
        pos.x = self.get_text_start_pos(page, line)
        pos.x -= text_width / 2

        # Center the box around the position
        pos.x -= width / 2

        return pos

    def get_text_start_pos(self, page, line):
        if page.number % 2 == 0:
            return self.page_width - self.outer_margin
        else:
            return self.outer_margin + self.get_text_width(line)

    def get_side_mark_pos(self, page, line, width):
        x = (self.outer_margin / 2) - (width / 2)
        if page.number % 2 == 0:
            x += self.get_text_start_pos(page, line)
        return x


class State:
    """Class holding document wide state."""

    def __init__(self):
        self.quarter = 1


class Document:
    """Class representing the main document and holding document-wide settings
       and state."""

    def __init__(self, chapters, filename):
        logger.debug("Initializing the document: %s", filename)

        self.settings = settings = Settings()
        self.state = State()
        self.surface = qh.PDFSurface.create(filename, (settings.page_width,
                                                       settings.page_height))
        self.shaper = Shaper(settings.body_font, settings.body_font_size)

        self.chapters = chapters

    def save(self):
        lines = self._create_lines()
        pages = self._create_pages(lines)

        logger.info("Drawing pages…")
        for page in pages:
            page.draw(self.surface, self.shaper, self.settings, self.state)

    def _create_lines(self):
        """Processes each chapter and creates lines for the whole document."""

        logger.info("Breaking text into lines…")

        lines = texwrap.ObjectList()
        for chapter in self.chapters:
            lines.extend(self._process_chapter(chapter))
        lines.add_closing_penalty()

        return lines

    def _create_pages(self, lines):
        """Breaks the lines into pages"""

        logger.info("Breaking lines into pages…")

        pages = [Page([], 1)]
        lengths = [self.settings.leading * self.settings.lines_per_page]
        breaks = lines.compute_breakpoints(lengths, tolerance=2)

        start = 0
        for i, breakpoint in enumerate(breaks[1:]):
            ratio = lines.compute_adjustment_ratio(start, breakpoint, i,
                                                   lengths)

            page = Page([], len(pages) + 1)
            for j in range(start, breakpoint):
                line = lines[j]
                if line.is_glue():
                    line.advance = line.compute_width(ratio)
                page.lines.append(line)

            while not page.lines[-1].is_box():
                page.lines.pop()

            pages.append(page)
            start = breakpoint + 1

        return pages

    def _process_chapter(self, chapter):
        """Shapes the text and breaks it into lines."""

        lengths = self.settings.text_widths
        nodes = self.shaper.shape_paragraph(chapter.text)
        breaks = nodes.compute_breakpoints(lengths, tolerance=2)

        lines = []
        if chapter.opening:
            box = self.shaper.shape_word("\uFDFD")
            lines.append(Line(self.settings.leading, box.advance, [box]))

        start = 0
        for i, breakpoint in enumerate(breaks[1:]):
            ratio = nodes.compute_adjustment_ratio(start, breakpoint, i,
                                                   lengths)

            line = Line(self.settings.leading, 0, [])
            for j in range(start, breakpoint):
                box = nodes[j]
                if box.is_glue():
                    box.advance = box.compute_width(ratio)
                line.boxes.append(box)

            while not line.boxes[-1].is_box():
                line.boxes.pop()

            line.width = sum([box.advance for box in line.boxes])
            lines.append(line)
            lines.append(Glue(0, 0, 0))

            start = breakpoint + 1

        # Allow stretching the glue between chapters.
        lines[-1].stretch = self.settings.leading

        return lines


class Chapter:
    """Class holding input text and metadata for a chapter."""

    def __init__(self, text, name, place, opening, verses):
        self.text = text
        self.name = name
        self.place = place
        self.opening = opening
        self.verses = verses


class Shaper:
    """Class for turning text into boxes and glue."""

    # Cache for shaped words.
    word_cache = {}

    def __init__(self, font_name, font_size):
        ft_face = ft.find_face(font_name)
        ft_face.set_char_size(size=font_size, resolution=qh.base_dpi)
        self.font = hb.Font.ft_create(ft_face)
        self.buffer = hb.Buffer.create()

    def shape_word(self, word):
        """
        Shapes a single word and returns the corresponding box. To speed things
        a bit, we cache the shaped words. We assume all our text is in Arabic
        script and language. The direction is almost always right-to-left,
        (we are cheating a bit to avoid doing proper bidirectional text as
        it is largely superfluous for us here).
        """

        assert word

        if word not in self.word_cache:
            self.buffer.clear_contents()
            self.buffer.add_str(word)
            # Everything is RTL except aya numbers and other digits-only words.
            if word.startswith("\u06DD") or word.isdigit():
                self.buffer.direction = hb.HARFBUZZ.DIRECTION_LTR
            else:
                self.buffer.direction = hb.HARFBUZZ.DIRECTION_RTL
            self.buffer.script = hb.HARFBUZZ.SCRIPT_ARABIC
            self.buffer.language = hb.Language.from_string("ar")

            hb.shape(self.font, self.buffer)

            glyphs, pos = self.buffer.get_glyphs()
            box = Box(pos.x, glyphs)

            # Flag boxes with “quarter” symbol, as it needs some special
            # handling later.
            if word.startswith("\u06DE"):
                box.quarter = True

            self.word_cache[word] = box

        return self.word_cache[word]

    def shape_paragraph(self, text):
        """
        Converts the text to a list of boxes and glues that the line breaker
        will work on. We basically split text into words and shape each word
        separately then put it into a box. We don’t try to preserve the
        context when shaping the words, as we know that our font does not
        do anything special around spaces, which in turn allows us to cache
        the shaped words.
        """
        nodes = texwrap.ObjectList()

        # Get the natural space advance
        space = self.shape_word(" ").advance

        # Split the text into words, treating space, newline and no-break space
        # as word separators.
        word = ""
        for ch in text.strip():
            if ch in (" ", "\n", "\u00A0"):
                nodes.append(self.shape_word(word))

                # Prohibit line breaking at no-break space.
                if ch == "\u00A0":
                    nodes.append(Penalty(0, texwrap.INFINITY))

                nodes.append(Glue(space, space / 2, space / 2))
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

    def __init__(self, lines, number):
        self.lines = lines
        self.number = number

    def draw(self, surface, shaper, settings, state):
        logger.debug("Drawing page %d…", self.number)

        if not self.lines:
            logger.debug("Leaving empty page blank")
            surface.show_page()
            return

        # Create a new FreeType face for Cairo, as sometimes Cairo mangles the
        # char size, breaking HarfBuzz positions when it uses the same face.
        ft_face = ft.find_face(settings.body_font)
        cr = self.cr = qh.Context.create(surface)
        cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))
        cr.set_font_size(settings.body_font_size)
        cr.set_source_colour(qh.Colour.grey(0))

        lines = self.lines
        pos = qh.Vector(0, settings.top_margin)
        for i, line in enumerate(lines):
            pos.x = settings.get_text_start_pos(self, i)
            text_width = settings.get_text_width(i)
            line.draw(cr, pos, text_width)
            if line.has_quarter():
                self._show_quarter(i, pos.y, state.quarter, shaper, settings)
                state.quarter += 1
            pos.y += line.advance

        # Show page number.
        box = shaper.shape_word(format_number(self.number))
        pos = settings.get_page_number_pos(self, box.advance)
        box.draw(cr, pos)

        cr.show_page()

    def _show_quarter(self, line, y, quarter, shaper, settings):
        """
        Draw the quarter, group and part text on the margin. A group is 4
        quarters, a part is 2 groups.
        """

        boxes = []
        num = quarter % 4
        if num:
            # A quarter.
            words = ("ربع", "نصف", "ثلاثة أرباع")
            boxes.append(shaper.shape_word(words[num - 1]))
            boxes.append(shaper.shape_word("الحزب"))
        else:
            # A group…
            group = format_number((quarter / 4) + 1)
            if quarter % 8:
                # … without a part.
                boxes.append(shaper.shape_word("حزب"))
                boxes.append(shaper.shape_word(group))
            else:
                # … with a part.
                part = format_number((quarter / 8) + 1)
                boxes.append(shaper.shape_word("حزب %s" % group))
                boxes.append(shaper.shape_word("جزء %s" % part))

        # We want the text to be smaller than the body size…
        scale = .8
        # … and the leading to be tighter.
        leading = settings.body_font_size

        w = max([box.advance for box in boxes])
        x = settings.get_side_mark_pos(self, line, w)
        # Center the boxes vertically around the line.
        # XXX: should use the box height / 2
        y -= leading / 2
        for box in boxes:
            # Center the box horizontally relative to the others
            offset = (w - box.advance) * scale / 2

            self.cr.save()
            self.cr.translate((x + offset, y))
            self.cr.scale((scale, scale))
            self.cr.show_glyphs(box.glyphs)
            self.cr.restore()

            y += leading


class Glue(texwrap.Glue):
    """Wraper around texwrap.Glue to hold our common API."""

    def draw(self, cr, pos, text_width=0):
        pass

    def has_quarter(self):
        return False


class Penalty(texwrap.Penalty):
    """Wraper around texwrap.Penalty to hold our common API."""

    def draw(self, cr, pos, text_width=0):
        pass

    def has_quarter(self):
        return False


class Box:
    """Class representing a word."""

    def __init__(self, advance, glyphs):
        self.advance = advance
        self.stretch = self.shrink = 0
        self.penalty = 0
        self.flagged = 0

        self.glyphs = glyphs
        self.quarter = False

    def is_glue(self):         return 0
    def is_box(self):          return 1
    def is_penalty(self):      return 0
    def is_forced_break(self): return 0

    def draw(self, cr, pos, text_width=0):
        cr.save()
        cr.translate(pos)
        cr.show_glyphs(self.glyphs)
        cr.restore()


class Line:
    """Class representing a line of text."""

    def __init__(self, advance, width, boxes):
        self.advance = advance
        self.width = width
        self.stretch = self.shrink = 0
        self.penalty = 0
        self.flagged = 0

        self.boxes = boxes

    def is_glue(self):         return 0
    def is_box(self):          return 1
    def is_penalty(self):      return 0
    def is_forced_break(self): return 0

    def has_quarter(self):
        return any([box.quarter for box in self.boxes if box.is_box()])

    def draw(self, cr, pos, text_width):
        # Center lines not equal to text width.
        if not math.isclose(self.width, text_width):
            pos.x -= (text_width - self.width) / 2

        for box in self.boxes:
            # We start drawing from the right edge of the text block,
            # and move to the left, thus the subtraction instead of
            # addition below.
            pos.x -= box.advance
            box.draw(cr, pos)


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

    path = os.path.join(args.datadir, "meta.txt")
    if os.path.isfile(path):
        with open(path, "r") as textfile:
            metadata = {}
            lines = [l.strip().split("\t") for l in textfile.readlines()]
            for num, line in enumerate(lines):
                num += 1
                metadata[num] = [line[0], line[1], True]
                if len(line) >= 3:
                    metadata[num][2] = int(line[2])
    else:
        logger.error("File not found: %s", path)
        sys.exit(1)

    chapters = []
    for i in args.chapters:
        path = os.path.join(args.datadir, "%03d.txt" % i)
        if os.path.isfile(path):
            with open(path, "r") as textfile:
                lines = textfile.readlines()
                chapter = Chapter("".join(lines), *metadata[i], len(lines))
                chapters.append(chapter)
        else:
            logger.error("File not found: %s", path)
            sys.exit(1)

    main(chapters, args.outfile)

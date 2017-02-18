import math

import harfbuzz as hb
import qahirah as qh
import texlib.wrap as texwrap

ft = qh.get_ft_lib()


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
        self.right_margin     = 100 # ~1.4in
        self.page_number_ypos = 460 # ~6.4in

    def get_text_width(self, line):
        if line >= len(self.text_widths):
            line = -1
        return self.text_widths[line]

    def get_page_number_pos(self, width):
        pos = qh.Vector(0, 0)

        # Center the number relative to the text box.
        text_width = self.get_text_width(self.lines_per_page - 1)
        pos.x = self.page_width - (text_width / 2) - self.right_margin
        pos.y = self.page_number_ypos

        # Center the box around the position
        pos.x -= width / 2

        return pos

    def get_line_start_pos(self, line, width=0):
        pos = qh.Vector(0, 0)
        pos.y = self.top_margin + (line * self.leading)
        pos.x = self.page_width - self.right_margin

        # Center lines not equal to text width.
        text_width = self.get_text_width(line)
        if not math.isclose(width, text_width):
            pos.x -= (text_width - width) / 2

        return pos


class State:
    """Class holding document wide state."""

    def __init__(self):
        self.quarter = 1

class Document:
    """Class representing the main document and holding document-wide settings
       and state."""

    def __init__(self, filename, chapters):
        self.settings = settings = Settings()
        self.state = State()
        self.surface = qh.PDFSurface.create(filename, (settings.page_width,
                                                       settings.page_height))
        self.shaper = Shaper(settings.body_font, settings.body_font_size)

        self.chapters = chapters

    def save(self):
        lines = self._create_lines()
        pages = self._create_pages(lines)

        for page in pages:
            page.draw(self.surface, self.shaper, self.settings, self.state)

    def _create_lines(self):
        """Processes each chapter and creates lines for the whole document."""

        lines = texwrap.ObjectList()
        for num, chapter in enumerate(self.chapters):
            lines.extend(self._process_chapter(chapter, num))
        lines.add_closing_penalty()

        return lines

    def _create_pages(self, lines):
        """Breaks the lines into pages"""

        pages = []
        lengths = [self.settings.leading * self.settings.lines_per_page]
        breaks = lines.compute_breakpoints(lengths, tolerance=2)

        start = 0
        for i, breakpoint in enumerate(breaks[1:]):
            page = Page([], i + 1)
            for j in range(start, breakpoint):
                line = lines[j]
                if line.is_box():
                    page.lines.append(line)
            pages.append(page)
            start = breakpoint + 1

        return pages

    def _process_chapter(self, text, num, opening=True):
        """Shapes the text and breaks it into lines."""

        lengths = self.settings.text_widths
        nodes = self.shaper.shape_paragraph(text)
        breaks = nodes.compute_breakpoints(lengths, tolerance=2)

        lines = []
        if opening:
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
            lines.append(texwrap.Glue(0, 0, 0))

            start = breakpoint + 1

        # Allow stretching the glue between chapters.
        lines[-1].stretch = self.settings.leading

        return lines

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
                    nodes.append(texwrap.Penalty(0, texwrap.INFINITY))

                nodes.append(texwrap.Glue(space, space / 2, space / 2))
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
        # Create a new FreeType face for Cairo, as sometimes Cairo mangles the
        # char size, breaking HarfBuzz positions when it uses the same face.
        ft_face = ft.find_face(settings.body_font)
        cr = self.cr = qh.Context.create(surface)
        cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))
        cr.set_font_size(settings.body_font_size)
        cr.set_source_colour(qh.Colour.grey(0))

        lines = self.lines
        for i, line in enumerate(lines):
            pos = settings.get_line_start_pos(i, line.width)
            for box in line.boxes:
                # We start drawing from the right edge of the text block, and
                # move to the left, thus the subtraction instead of addition
                # below.
                pos.x -= box.advance
                if box.is_box():
                    cr.save()
                    cr.translate(pos)
                    cr.show_glyphs(box.glyphs)
                    cr.restore()
                    if box.quarter:
                        self._show_quarter(pos.y, state.quarter, shaper,
                                           settings)
                        state.quarter += 1

        # Show page number.
        box = shaper.shape_word(format_number(self.number))
        pos = settings.get_page_number_pos(box.advance)
        cr.save()
        cr.translate(pos)
        cr.show_glyphs(box.glyphs)
        cr.restore()

        cr.show_page()

    def _show_quarter(self, y, quarter, shaper, settings):
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
        x = settings.page_width - settings.right_margin / 2 - w / 2
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

def main(data, filename):
    document = Document(filename, data)
    document.save()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: %s text_file [text_file2…] output.pdf" % sys.argv[0])
        sys.exit(1)

    data = []
    for arg in sys.argv[1:-1]:
        with open(arg, "r") as textfile:
            data.append(textfile.read())
    main(data, sys.argv[-1])

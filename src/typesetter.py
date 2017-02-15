import harfbuzz as hb
import qahirah as qh
import texlib.wrap as texwrap

ft = qh.get_ft_lib()


class Box:
    """Class representing a word."""

    def __init__(self, width, glyphs=None):
        self.width = width
        self.stretch = self.shrink = 0
        self.penalty = 0
        self.flagged = 0

        self.glyphs = glyphs
        self.quarter = False

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
        self.text_width       = 205 # ~2.84in
        self.page_width       = 396 # 5.5in
        self.page_height      = 540 # 7.5in
        # From top of page to first baseline.
        self.top_margin       = 105 # ~1.46
        self.right_margin     = 100 # ~1.4in
        self.page_number_ypos = 460 # ~6.4in

    def get_page_number_pos(self, width):
        pos = qh.Vector(0, 0)

        # Center the number relative to the text box.
        pos.x = self.page_width - (self.text_width / 2) - self.right_margin
        pos.y = self.page_number_ypos

        # Center the box around the position
        pos.x -= width / 2

        return pos

    def get_line_start_pos(self, line, width=0):
        pos = qh.Vector(0, 0)
        pos.y = self.top_margin + (line * self.leading)
        pos.x = self.page_width - self.right_margin
        if width:
            # Callers give width only for the last line, so we can center it.
            pos.x -= (self.text_width - width) / 2

        return pos


class State:
    """Class holding document wide state."""

    def __init__(self):
        self.line = 0
        self.page = 1
        self.quarter = 1

class Document:
    """Class representing the main document and holding document-wide settings
       and state."""

    def __init__(self, filename, chapters):
        self.settings = settings = Settings()
        self.state = State()
        self.surface = qh.PDFSurface.create(filename, (settings.page_width,
                                                       settings.page_height))

        self.chapters = chapters

    def save(self):
        for num, chapter in enumerate(self.chapters):
            self._output_chapter(chapter, num)

    def _output_chapter(self, text, number, opening=True):
        typesetter = Typesetter(text,
                                self.surface,
                                self.settings.body_font,
                                self.settings.body_font_size,
                                self.settings,
                                self.state,
                                opening)
        typesetter.output()

class Typesetter:

    # Cache for shaped words.
    word_cache = {}

    def __init__(self, text, surface, font_name, font_size, settings, state,
                 opening=True):
        self.text = text
        self.state = state
        self.settings = settings
        self.lengths = [self.settings.text_width]
        self.opening = opening

        ft_face = ft.find_face(font_name)
        ft_face.set_char_size(size=font_size, resolution=qh.base_dpi)
        self.font = hb.Font.ft_create(ft_face)
        self.buffer = hb.Buffer.create()

        # Create a new FreeType face for Cairo, as sometimes Cairo mangles the
        # char size, breaking HarfBuzz positions when it uses the same face.
        ft_face = ft.find_face(font_name)
        cr = self.cr = qh.Context.create(surface)
        cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))
        cr.set_font_size(font_size)

    def output(self):
        self._show_opening()
        self._create_nodes()
        self._compute_breaks()
        self._draw_output()

        # Show last page number.
        # XXX: move to Document.
        self._show_page_number()

    def _shape_word(self, word):
        """
        Shapes a single word and returns the corresponding box. To speed things
        a bit, we cache the shaped words. We assume all our text is in Arabic
        script and language. The direction is almost always right-to-left,
        (we are cheating a bit to avoid doing proper bidirectional text as
        it is largely superfluous for us here).
        """

        if not word:
            return Box(0)

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

    def _create_nodes(self):
        """
        Converts the text to a list of boxes and glues that the line breaker
        will work on. We basically split text into words and shape each word
        separately then put it into a box. We don’t try to preserve the
        context when shaping the words, as we know that our font does not
        do anything special around spaces, which in turn allows us to cache
        the shaped words.
        """
        nodes = self.nodes = texwrap.ObjectList()

        # Get the natural space width, and calculate its stretch and shrink.
        space_gid = self.font.get_nominal_glyph(ord(" "))
        space_adv = self.font.get_glyph_h_advance(space_gid)
        space_glue = texwrap.Glue(space_adv, space_adv / 2, space_adv / 2)

        buf = self.buffer
        font = self.font

        # Split the text into words, treating space, newline and no-break space
        # as word separators.
        word = ""
        for ch in self.text:
            if ch in (" ", "\n", "\u00A0"):
                self.nodes.append(self._shape_word(word))

                # Prohibit line breaking at no-break space.
                if ch == "\u00A0":
                    nodes.append(texwrap.Penalty(0, texwrap.INFINITY))

                nodes.append(space_glue)
                word = ""
            else:
                word += ch
        self.nodes.append(self._shape_word(word)) # last word

        nodes.add_closing_penalty()

    def _compute_breaks(self):
        self.breaks = self.nodes.compute_breakpoints(self.lengths, tolerance=2)

    def _format_number(self, number):
        """Format number to Arabic-Indic digits."""

        number = int(number)
        return "".join([chr(ord(c) + 0x0630) for c in str(number)])

    def _show_page_number(self):
        box = self._shape_word(self._format_number(self.state.page))
        pos = self.settings.get_page_number_pos(box.width)

        self.cr.save()
        self.cr.translate(pos)
        self.cr.show_glyphs(box.glyphs)
        self.cr.restore()

    def _show_quarter(self, y):
        """
        Draw the quarter, group and part text on the margin. A group is 4
        quarters, a part is 2 groups.
        """

        boxes = []
        num = self.state.quarter % 4
        if num:
            # A quarter.
            words = ("ربع", "نصف", "ثلاثة أرباع")
            boxes.append(self._shape_word(words[num - 1]))
            boxes.append(self._shape_word("الحزب"))
        else:
            # A group…
            group = self._format_number((self.state.quarter / 4) + 1)
            if self.state.quarter % 8:
                # … without a part.
                boxes.append(self._shape_word("حزب"))
                boxes.append(self._shape_word(group))
            else:
                # … with a part.
                part = self._format_number((self.state.quarter / 8) + 1)
                boxes.append(self._shape_word("حزب %s" % group))
                boxes.append(self._shape_word("جزء %s" % part))

        # We want the text to be smaller than the body size…
        scale = .8
        # … and the leading to be tighter.
        leading = self.settings.body_font_size

        w = max([box.width for box in boxes])
        x = self.settings.page_width - self.settings.right_margin / 2 - w / 2
        # Center the boxes vertically around the line.
        # XXX: should use the box height / 2
        y -= leading / 2
        for box in boxes:
            # Center the box horizontally relative to the others
            offset = (w - box.width) * scale / 2

            self.cr.save()
            self.cr.translate((x + offset, y))
            self.cr.scale((scale, scale))
            self.cr.show_glyphs(box.glyphs)
            self.cr.restore()

            y += leading

    def _finish_page(self):
         self._show_page_number()
         self.cr.show_page()
         self.state.page += 1
         self.state.line = 0

    def _show_opening(self):
        if not self.opening:
            return

        # Finish the page of only one line is left.
        if self.state.line == self.settings.lines_per_page - 1:
            self._finish_page()

        box = self._shape_word("\uFDFD")
        pos = self.settings.get_line_start_pos(self.state.line, box.width)
        pos.x -= box.width
        self.cr.save()
        self.cr.translate(pos)
        self.cr.show_glyphs(box.glyphs)
        self.cr.restore()

        self.state.line += 1

    def _draw_output(self):
        self.cr.set_source_colour(qh.Colour.grey(0))

        line_start = 0
        line = 0
        for breakpoint in self.breaks[1:]:
            if line == len(self.breaks) - 2:
                # Last line, pass the width to get a centered position.
                width = self.nodes.measure_width(line_start, breakpoint)
                if width == 0.0:
                    # Skip empty last line.
                    continue
                pos = self.settings.get_line_start_pos(self.state.line, width)
            else:
                pos = self.settings.get_line_start_pos(self.state.line)

            ratio = self.nodes.compute_adjustment_ratio(line_start, breakpoint, line, self.lengths)
            line += 1
            self.state.line += 1

            for i in range(line_start, breakpoint):
                # We start drawing from the right edge of the text box, then
                # move to the left, thus the subtraction instead of addition
                # below.
                box = self.nodes[i]
                if box.is_glue():
                    pos.x -= box.compute_width(ratio)
                elif box.is_box() and box.glyphs:
                    pos.x -= box.width
                    self.cr.save()
                    self.cr.translate(pos)
                    self.cr.show_glyphs(box.glyphs)
                    self.cr.restore()
                    if box.quarter:
                        self._show_quarter(pos.y)
                        self.state.quarter += 1

            line_start = breakpoint + 1

            # The page had enough lines, start new page.
            if self.state.line == self.settings.lines_per_page:
                self._finish_page()

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

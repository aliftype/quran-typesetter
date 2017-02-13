import harfbuzz as hb
import qahirah as qh
import texlib.wrap as texwrap

ft = qh.get_ft_lib()


class Box:
    """Class representing a word. Boxes have a fixed width that doesn't change.
    """

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
        self.top_margin       = 105 # ~1.46, from top of page to first baseline
        self.right_margin     = 100 # ~1.4in
        self.page_number_ypos = 460 # ~6.4in

    def get_page_number_pos(self):
        pos = qh.Vector(0, 0)

        # Center the number relative to the text box.
        pos.x = self.page_width - self.text_width / 2 - self.right_margin
        pos.y = self.page_number_ypos

        return pos

class State:
    """Class holding document wide state."""

    def __init__(self):
        self.line = 0
        self.page = 0
        self.quarter = 0

class Document:
    """Class representing the main document and holding document-wide settings
       and state."""

    def __init__(self, surface, settings):
        self.surface = surface
        self.settings = settings
        self.state = State()

    def chapter(self, text, number, opening=True):
        if opening:
            typesetter = Typesetter("\uFDFD",
                                    self.surface,
                                    self.settings.body_font,
                                    self.settings.body_font_size,
                                    self.settings,
                                    self.state)
            typesetter.output()

        typesetter = Typesetter(text,
                                self.surface,
                                self.settings.body_font,
                                self.settings.body_font_size,
                                self.settings,
                                self.state)
        typesetter.output()

class Typesetter:

    def __init__(self, text, surface, font_name, font_size, settings, state):
        self.text = text
        self.state = state
        self.settings = settings
        self.lengths = [self.settings.text_width]

        ft_face = ft.find_face(font_name)
        ft_face.set_char_size(size=font_size, resolution=qh.base_dpi)
        self.font = hb.Font.ft_create(ft_face)
        self.buffer = hb.Buffer.create()

        # Create a new FreeType face for Cairo, as sometimes cairo mangles the
        # char size, breaking HarfBuzz positiong when it uses the same face.
        ft_face = ft.find_face(font_name)
        ft_face.set_char_size(size=font_size, resolution=qh.base_dpi)
        cr = self.cr = qh.Context.create(surface)
        cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))
        cr.set_font_size(font_size)

        self.word_cache = {}

    def output(self):
        self._create_nodes()
        self._compute_breaks()
        self._draw_output()

        # Show last page number.
        # XXX: move to Document.
        self._show_page_number()

    def _shape_word(self, word):
        if not word:
            return Box(0)

        if word not in self.word_cache:
            self.buffer.clear_contents()
            self.buffer.add_str(word)
            if word.startswith("\u06DD") or word.isdigit():
                self.buffer.direction = hb.HARFBUZZ.DIRECTION_LTR
            else:
                self.buffer.direction = hb.HARFBUZZ.DIRECTION_RTL
            self.buffer.script = hb.HARFBUZZ.SCRIPT_ARABIC
            self.buffer.language = hb.Language.from_string("ar")

            hb.shape(self.font, self.buffer)

            glyphs, pos = self.buffer.get_glyphs()
            box = Box(pos.x, glyphs)
            if word.startswith("\u06DE"):
                box.quarter = True
            self.word_cache[word] = box

        return self.word_cache[word]

    def _create_nodes(self):
        nodes = self.nodes = texwrap.ObjectList()

        space_gid = self.font.get_nominal_glyph(ord(" "))
        space_adv = self.font.get_glyph_h_advance(space_gid)
        space_glue = texwrap.Glue(space_adv, space_adv / 2, space_adv / 2)

        buf = self.buffer
        font = self.font

        word = ""
        for ch in self.text:
            if ch in (" ", "\n", "\u00A0"):
                self.nodes.append(self._shape_word(word))

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
        return "".join([chr(ord(c) + 0x0630) for c in str(number)])

    def _show_page_number(self):
        box = self._shape_word(self._format_number(self.state.page + 1))

        pos = self.settings.get_page_number_pos()
        # Center the box around the position
        pos.x -= box.width / 2

        self.cr.save()
        self.cr.translate(pos)
        self.cr.show_glyphs(box.glyphs)
        self.cr.restore()

    def _show_quarter(self, y):
        boxes = []
        num = self.state.quarter % 4
        if num == 0:
            boxes.append(self._shape_word("ربع"))
            boxes.append(self._shape_word("الحزب"))
        elif num == 1:
            boxes.append(self._shape_word("نصف"))
            boxes.append(self._shape_word("الحزب"))
        elif num == 2:
            boxes.append(self._shape_word("ثلاثة أرباع"))
            boxes.append(self._shape_word("الحزب"))
        else:
            num = int((self.state.quarter + 1) / 4) + 1
            boxes.append(self._shape_word("حزب"))
            boxes.append(self._shape_word(self._format_number(num)))

        line_height = self.settings.body_font_size
        scale = .8

        w = max([box.width for box in boxes])
        x = self.settings.page_width - self.settings.right_margin / 2 - w / 2
        y -= line_height / 2
        for box in boxes:
            offset = (w - box.width) * scale / 2

            self.cr.save()
            self.cr.translate((x + offset, y))
            self.cr.scale((scale, scale))
            self.cr.show_glyphs(box.glyphs)
            self.cr.restore()

            y += line_height

    def _draw_output(self):
        self.cr.set_source_colour(qh.Colour.grey(0))

        line_start = 0
        line = 0
        pos = qh.Vector(0, self.settings.top_margin + self.state.line * self.settings.leading)
        for breakpoint in self.breaks[1:]:
            offset = 0
            if line == len(self.breaks) - 2:
                # center last line
                offset = (self.settings.text_width - self.nodes.measure_width(line_start, breakpoint)) / 2

            pos.x = self.settings.page_width - self.settings.right_margin - offset

            ratio = self.nodes.compute_adjustment_ratio(line_start, breakpoint, line, self.lengths)
            line += 1
            self.state.line += 1
            for i in range(line_start, breakpoint):
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

            pos.y += self.settings.leading

            if self.state.line % self.settings.lines_per_page == 0:
                self._show_page_number()
                self.cr.show_page()
                pos.y = self.settings.top_margin
                self.state.page += 1

def main(text, filename):
    settings = Settings()
    surface = qh.PDFSurface.create(filename, (settings.page_width, settings.page_height))

    document = Document(surface, settings)
    document.chapter(text, 0)

if __name__ == "__main__":
    import sys
    with open(sys.argv[1], "r") as textfile:
        text = textfile.read()
        main(text, sys.argv[2])

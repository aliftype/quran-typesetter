import harfbuzz as hb
import qahirah as qh
import texlib.wrap as texwrap

ft = qh.get_ft_lib()


class Settings:
    """Class holding document wide settings."""

    def __init__(self):
        self.lines_per_page = 0
        self.text_width     = 0
        self.page_width     = 0
        self.page_height    = 0
        self.top_margin     = 0
        self.right_margin   = 0
        self.body_font      = ""
        self.body_font_size = 0
        self.leading        = 0

class State:
    """Class holding document wide state."""

    def __init__(self):
        self.line = 0
        self.page = 0

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
        self.text           = text
        self.leading        = settings.leading
        self.lines_per_page = settings.lines_per_page
        self.text_width     = settings.text_width
        self.page_width     = settings.page_width
        self.top_margin     = settings.top_margin
        self.right_margin   = settings.right_margin

        self.state          = state

        ft_face = ft.find_face(font_name)
        ft_face.set_char_size(size=font_size, resolution=qh.base_dpi)
        self.font = hb.Font.ft_create(ft_face)
        self.buffer = hb.Buffer.create()

        cr = self.cr = qh.Context.create(surface)
        cr.set_font_face(qh.FontFace.create_for_ft_face(ft_face))
        cr.set_font_size(font_size)

        self.word_cache = {}

    def output(self):
        self._create_nodes()
        self._compute_breaks()
        self._draw_output()

    def _shape_word(self, word):
        if not word:
            return texwrap.Box(0)

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
            self.word_cache[word] = texwrap.Box(pos.x, glyphs)

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
        lengths = [self.text_width]
        self.breaks = self.nodes.compute_breakpoints(lengths, tolerance=2)

    def _format_number(self, number):
        return "".join([chr(ord(c) + 0x0630) for c in str(number)])

    def _show_page_number(self):
        box = self._shape_word(self._format_number(self.state.page + 1))

        pos = qh.Vector(0, 0)
        pos.x = self.page_width - (self.text_width / 2) - self.right_margin
        pos.y = self.top_margin + (self.lines_per_page + 1) * self.leading

        pos.x -= box.width / 2

        self.cr.save()
        self.cr.translate(pos)
        self.cr.show_glyphs(box.character)
        self.cr.restore()

    def _draw_output(self):
        self.cr.set_source_colour(qh.Colour.grey(0))

        lengths = [self.text_width]
        line_start = 0
        line = 0
        pos = qh.Vector(0, self.top_margin + self.state.line * self.leading)
        for breakpoint in self.breaks[1:]:
            offset = 0
            if line == len(self.breaks) - 2:
                # center last line
                offset = self.text_width - self.nodes.measure_width(line_start,
                                                                    breakpoint)
                offset /= 2

            pos.x = self.page_width - self.right_margin - offset

            ratio = self.nodes.compute_adjustment_ratio(line_start, breakpoint, line, lengths)
            line += 1
            self.state.line += 1
            for i in range(line_start, breakpoint):
                box = self.nodes[i]
                if box.is_glue():
                    pos.x -= box.compute_width(ratio)
                elif box.is_box() and box.character:
                    pos.x -= box.width
                    self.cr.save()
                    self.cr.translate(pos)
                    self.cr.show_glyphs(box.character)
                    self.cr.restore()
                else:
                    pass
            line_start = breakpoint + 1

            pos.y += self.leading

            if self.state.line % self.lines_per_page == 0:
                self._show_page_number()
                self.cr.show_page()
                pos.y = self.top_margin
                self.state.page += 1

def main(text, filename):
    settings = Settings()
    settings.body_font = "Amiri Quran"
    settings.body_font_size = 10
    settings.leading = 29 # ~0.4in

    settings.text_width = 205 # ~2.84in
    settings.lines_per_page = 12

    settings.top_margin = 105 # ~1.46, from top of page to first baseline
    settings.right_margin = 100 # ~1.4in

    settings.page_width = 396 # 5.5in
    settings.page_height = 540 # 7.5in

    surface = qh.PDFSurface.create(filename, (settings.page_width, settings.page_height))

    document = Document(surface, settings)
    document.chapter(text, 0)

if __name__ == "__main__":
    import sys
    with open(sys.argv[1], "r") as textfile:
        text = textfile.read()
        main(text, sys.argv[2])

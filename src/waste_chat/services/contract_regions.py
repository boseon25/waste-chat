"""Locate printed contract values after Platypus has wrapped and paginated the table."""
from itertools import groupby
from xml.sax.saxutils import escape

from reportlab.platypus import Paragraph


class _TrackedValue(Paragraph):
    def split(self, availWidth, availHeight):
        pieces = super().split(availWidth, availHeight)
        for piece in pieces:
            piece.regions = self.regions
            piece.page_height = self.page_height
            piece.single_key = self.single_key
        return pieces

    def draw(self):
        # drawOn has already translated the canvas to the final position of
        # this (possibly split) paragraph. Contract value lines have fixed leading.
        if self.single_key:
            self._record(self.single_key, 0, self.height)
        else:
            keys = []
            for line in self.blPara.lines:
                printed_keys = {getattr(frag, 'contract_field', None)
                                for frag in getattr(line, 'words', ()) if frag.text.strip()}
                keys.append(printed_keys.pop() if len(printed_keys) == 1 else None)
            start = 0
            for key, lines in groupby(keys):
                end = start + sum(1 for _ in lines)
                if key:
                    self._record(key, self.height - end * self.style.leading,
                                 self.height - start * self.style.leading)
                start = end
        super().draw()

    def _record(self, key, bottom, top):
        left, upper_y = self.canv.absolutePosition(0, top)
        right, lower_y = self.canv.absolutePosition(self.width, bottom)
        self.regions.append((key, self.canv.getPageNumber() - 1, left, right,
                             self.page_height - upper_y, self.page_height - lower_y))


def value_paragraph(parts, style, regions, page_height):
    """Parts are (field key, printed text) pairs, joined just like the original cell."""
    lines = [(key, line) for key, value in parts for line in value.split('\n')]
    markup = '<br/>'.join(escape(line) for _, line in lines)
    if regions is None:
        return Paragraph(markup, style)
    p = _TrackedValue(markup, style)
    p.page_height = page_height
    p.regions = regions
    p.single_key = parts[0][0] if len(parts) == 1 else None
    if not p.single_key:
        # Platypus clones these fragments when wrapping and splitting. Tags do
        # not add PDF content; they distinguish adjacent fields in a party cell.
        line = 0
        for frag in p.frags:
            frag.contract_field = lines[line][0]
            if getattr(frag, 'lineBreak', False):
                line += 1
    return p

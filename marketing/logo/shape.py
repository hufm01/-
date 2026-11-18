"""Tiny Arabic shaper: text -> SVG path using a TTF (fontTools only)."""
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

DUAL = set("بتثجحخسشصضطظعغفقكلمنهيئى")
RIGHT = set("اأإآدذرزوؤة")

class Shaper:
    def __init__(self, path):
        self.f = TTFont(path)
        self.cmap = self.f.getBestCmap()
        self.gs = self.f.getGlyphSet()
        self.upm = self.f['head'].unitsPerEm
        self.hmtx = self.f['hmtx']
        self.forms = {t: {} for t in ('init', 'medi', 'fina', 'isol')}
        g = self.f['GSUB'].table
        for fr in g.FeatureList.FeatureRecord:
            if fr.FeatureTag in self.forms:
                for li in fr.Feature.LookupListIndex:
                    lk = g.LookupList.Lookup[li]
                    for st in lk.SubTable:
                        if lk.LookupType == 7:
                            st = st.ExtSubTable
                        if hasattr(st, 'mapping'):
                            self.forms[fr.FeatureTag].update(st.mapping)

    def glyphs(self, text):
        chars = list(text)
        out = []
        def joins_left(c): return c in DUAL
        def joins_right(c): return c in DUAL or c in RIGHT
        for i, c in enumerate(chars):
            g = self.cmap[ord(c)]
            if c in DUAL or c in RIGHT:
                prev = chars[i-1] if i > 0 else None
                nxt = chars[i+1] if i+1 < len(chars) else None
                a = prev is not None and joins_left(prev)        # connects to previous
                b = c in DUAL and nxt is not None and joins_right(nxt)  # connects to next
                form = {(True, True): 'medi', (False, True): 'init', (True, False): 'fina', (False, False): 'isol'}[(a, b)]
                g = self.forms[form].get(g, g)
            out.append(g)
        return out

    def path(self, text, size, x, y, rtl=None, anchor='middle', tracking=0):
        """Return (svg path d, width). x,y = anchor point on baseline."""
        if rtl is None:
            rtl = any('\u0600' <= ch <= '\u06ff' for ch in text)
        gl = self.glyphs(text)
        s = size / self.upm
        adv = [self.hmtx[g][0] for g in gl]
        total = (sum(adv) + tracking * (len(gl) - 1)) * s
        start = {'middle': x - total / 2, 'start': x if not rtl else x - total, 'end': x - total if not rtl else x}[anchor]
        order = list(zip(gl, adv))
        if rtl:
            order = order[::-1]  # visual left-to-right
        pen = SVGPathPen(self.gs)
        cx = 0
        for g, a in order:
            tp = TransformPen(pen, (s, 0, 0, -s, start + cx * s, y))
            self.gs[g].draw(tp)
            cx += a + tracking
        return pen.getCommands(), total

from fontTools.pens.boundsPen import BoundsPen
def bbox(sh, text, size, x, y, anchor='middle', tracking=0):
    rtl = any('\u0600' <= ch <= '\u06ff' for ch in text)
    gl = sh.glyphs(text); s = size / sh.upm
    adv = [sh.hmtx[g][0] for g in gl]
    total = (sum(adv) + tracking * (len(gl) - 1)) * s
    start = {'middle': x - total / 2, 'start': x if not rtl else x - total, 'end': x - total if not rtl else x}[anchor]
    order = list(zip(gl, adv))[::-1] if rtl else list(zip(gl, adv))
    bp = BoundsPen(sh.gs); cx = 0
    for g, a in order:
        sh.gs[g].draw(TransformPen(bp, (s, 0, 0, -s, start + cx * s, y))); cx += a + tracking
    return bp.bounds  # xmin, ymin, xmax, ymax in svg coords

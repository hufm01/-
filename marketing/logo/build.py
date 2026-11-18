import sys
from shape import Shaper, bbox
F = Shaper(r'C:/Windows/Fonts/DUBAI-BOLD.TTF')
OUT = sys.argv[1]

GRAD = '''<linearGradient id="g" gradientUnits="userSpaceOnUse" x1="290" y1="450" x2="930" y2="110">
  <stop offset="0" stop-color="#e11d48"/><stop offset=".38" stop-color="#f43f5e"/>
  <stop offset=".78" stop-color="#f97316"/><stop offset="1" stop-color="#fbbf24"/></linearGradient>'''

def mark(hole):
    word, _ = F.path('لقطة', 300, 600, 400)
    pct, _ = F.path("%", 92, 0, 32)
    # frame: rounded rect 280..920 x 130..450, open at the top-right corner for the tag
    frame = ('M 790 130 H 326 A 46 46 0 0 0 280 176 V 404 A 46 46 0 0 0 326 450 '
             'H 874 A 46 46 0 0 0 920 404 V 250')
    tag = f'''<g transform="translate(908 144) rotate(-32)">
  <path d="M -78 -46 H 46 L 92 0 L 46 46 H -78 Z" fill="url(#tg)" stroke="url(#tg)" stroke-width="18" stroke-linejoin="round"/>
  <circle cx="58" cy="0" r="10" fill="{hole}"/>
  <path d="{pct}" transform="translate(-16 0)" fill="{hole}"/>
  <path d="M 68 0 C 110 -4 128 -40 104 -62 C 86 -78 60 -60 74 -40" fill="none" stroke="#fbbf24" stroke-width="7" stroke-linecap="round"/>
</g>'''
    return f'''<path d="{frame}" fill="none" stroke="url(#g)" stroke-width="16" stroke-linecap="round"/>
<path d="{word}" fill="url(#g)"/>
{tag}'''

TG = '''<linearGradient id="tg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#f97316"/><stop offset="1" stop-color="#fbbf24"/></linearGradient>'''

def full(bg, fg, sub, name):
    laqta, _ = F.path('LAQTA', 112, 600, 610, tracking=70)
    tagline, _ = F.path('كل عروض الموصل بمكان واحد', 50, 600, 700)
    bgr = f'<rect width="1200" height="800" fill="{bg}"/>' if bg else ''
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" width="1200" height="800">
<defs>{GRAD}{TG}</defs>{bgr}
{mark(bg or "#0a0a0f")}
<path d="{laqta}" fill="{fg}"/>
<path d="{tagline}" fill="{sub}"/>
</svg>'''
    open(f'{OUT}/{name}.svg', 'w', encoding='utf-8').write(svg)

def icon(name):
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" width="1080" height="1080">
<defs>{GRAD}{TG}</defs><rect width="1080" height="1080" fill="#0a0a0f"/>
<g transform="translate(540 560) scale(1.12) translate(-608 -290)">{mark("#0a0a0f")}</g>
</svg>'''
    open(f'{OUT}/{name}.svg', 'w', encoding='utf-8').write(svg)

full('#0a0a0f', '#ffffff', '#cbd5e1', 'laqta-logo-dark')
full('#ffffff', '#0f172a', '#475569', 'laqta-logo-light')
icon('laqta-icon')

def header_mark(name, vb='262 20 744 448'):
    x, y, w, h = map(float, vb.split())
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{w:g}" height="{h:g}">
<defs>{GRAD}{TG}</defs>
{mark("#0a0a0f")}
</svg>'''
    open(f'{OUT}/{name}.svg', 'w', encoding='utf-8').write(svg)

header_mark('laqta-mark')

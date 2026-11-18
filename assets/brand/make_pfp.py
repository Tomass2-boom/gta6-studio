"""YouTube profile picture, drawn in code (original design, our caption colours, Nunito Black OFL).
A big sticker-style "6" with a play button in its loop = "GTA 6 clips". 800x800; everything sits inside
the centre circle YouTube crops to. Run: uv run python assets/brand/make_pfp.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
FONT = HERE.parent / "fonts" / "Nunito-Black.ttf"
S = 800
VARIANTS = {"yellow": ("#FFE600", "#FF2D2D"), "red": ("#FF2D2D", "#FFE600"), "green": ("#3CFF3C", "#FFE600")}


def glyph_mask(size: int) -> Image.Image:
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    f = ImageFont.truetype(str(FONT), size)
    x0, y0, x1, y1 = d.textbbox((0, 0), "6", font=f)
    d.text(((S - (x1 - x0)) / 2 - x0, (S - (y1 - y0)) / 2 - y0 - 8), "6", font=f, fill=255)
    return m


def hole(m: Image.Image):
    """The 6's closed loop: background pixels not reachable from the corner."""
    t = m.point(lambda v: 255 if v > 127 else 0).convert("L")
    ImageDraw.floodfill(t, (0, 0), 255)
    px = [(x, y) for y in range(0, S, 2) for x in range(0, S, 2) if t.getpixel((x, y)) == 0]
    xs, ys = [p[0] for p in px], [p[1] for p in px]
    return sum(xs) / len(xs), sum(ys) / len(ys), (max(xs) - min(xs)), (max(ys) - min(ys))


def make(name: str, fill: str, accent: str):
    bg = Image.new("RGB", (S, S), "#0B0B0F")
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse((110, 110, 690, 690), fill=170)
    bg.paste(Image.new("RGB", (S, S), accent), (0, 0), glow.filter(ImageFilter.GaussianBlur(140)).point(lambda v: v // 3))
    m = glyph_mask(640).rotate(-7, resample=Image.BICUBIC, center=(S / 2, S / 2))  # slight lean = energy
    stroke = m.filter(ImageFilter.MaxFilter(31))                       # thick black sticker outline
    shadow = Image.new("L", (S, S), 0); shadow.paste(stroke, (14, 18))  # hard offset shadow, accent colour
    bg.paste(Image.new("RGB", (S, S), accent), (0, 0), shadow)
    bg.paste(Image.new("RGB", (S, S), "black"), (0, 0), stroke)
    bg.paste(Image.new("RGB", (S, S), fill), (0, 0), m)
    cx, cy, hw, hh = hole(m)
    loop = m.point(lambda v: 255 if v > 127 else 0)                    # the loop becomes a lit "screen"
    ImageDraw.floodfill(loop, (0, 0), 255)
    loop = loop.point(lambda v: 255 - v).filter(ImageFilter.MinFilter(13))
    bg.paste(Image.new("RGB", (S, S), accent), (0, 0), loop.filter(ImageFilter.GaussianBlur(1)))
    r = min(hw, hh) * 0.30                                             # play triangle on the screen
    cx += r * 0.12
    tri = [(cx - r * 0.6, cy - r * 0.95), (cx - r * 0.6, cy + r * 0.95), (cx + r * 0.95, cy)]
    tm = Image.new("L", (S * 4, S * 4), 0)                             # 4x supersampled for clean edges
    ImageDraw.Draw(tm).polygon([(x * 4, y * 4) for x, y in tri], fill=255)
    tm = tm.resize((S, S), Image.LANCZOS)
    bg.paste(Image.new("RGB", (S, S), "black"), (0, 0), tm)
    out = HERE / f"pfp_{name}.png"
    bg.save(out)
    return out


if __name__ == "__main__":
    for k, (f, a) in VARIANTS.items():
        print(make(k, f, a))

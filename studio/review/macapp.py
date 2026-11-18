"""Build 'GTA6 Studio.app': a tiny bundle whose launcher sets PATH, goes to the project folder and
runs the control app with the project's own Python, so no Terminal window appears."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import ROOT
from .runner import EXTRA_PATH
from .window import ICON

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>GTA6 Studio</string>
  <key>CFBundleDisplayName</key><string>GTA6 Studio</string>
  <key>CFBundleIdentifier</key><string>local.gta6studio.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>GTA6Studio</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""


def launcher(root: Path = ROOT) -> str:
    q = lambda p: "'" + str(p).replace("'", "'\\''") + "'"
    path = ":".join(EXTRA_PATH + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    return f"""#!/bin/bash
# GTA6 Studio launcher (made by `uv run studio make-app`). Opens the control app window.
export PATH={q(path)}
cd {q(root)} || exit 1
mkdir -p data/logs
[ -x .venv/bin/python ] || uv sync -q >> data/logs/app-window.log 2>&1
exec .venv/bin/python -m studio.review.window >> data/logs/app-window.log 2>&1
"""


def make_icon(png: Path = ICON):
    """1024 px app icon: dark rounded square, pink 'GTA6', white 'STUDIO' (Nunito, OFL)."""
    from PIL import Image, ImageDraw, ImageFont
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg = Image.new("RGBA", (S, S))
    top, bot = (60, 20, 90), (15, 15, 30)
    for y in range(S):
        k = y / S
        ImageDraw.Draw(bg).line([(0, y), (S, y)], fill=tuple(int(top[i] * (1 - k) + bot[i] * k) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([90, 90, S - 90, S - 90], radius=190, fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)
    font = ROOT / "assets" / "fonts" / "Nunito-Black.ttf"
    f1, f2 = ImageFont.truetype(str(font), 300), ImageFont.truetype(str(font), 120)
    d.text((S / 2, 430), "GTA6", font=f1, fill=(236, 72, 153, 255), anchor="mm")
    d.text((S / 2, 660), "STUDIO", font=f2, fill=(255, 255, 255, 255), anchor="mm")
    d.ellipse([S / 2 - 34, 760, S / 2 + 34, 828], fill=(239, 68, 68, 255))  # the "recording" dot
    png.parent.mkdir(parents=True, exist_ok=True)
    img.save(png)
    return png


def make_icns(png: Path, out: Path):
    with tempfile.TemporaryDirectory() as t:
        iconset = Path(t) / "icon.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = s * scale
                name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                               capture_output=True, check=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def install(dest: Path) -> Path:
    if not ICON.exists():
        make_icon()
    app = dest / "GTA6 Studio.app"
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    (app / "Contents" / "Info.plist").write_text(PLIST)
    exe = app / "Contents" / "MacOS" / "GTA6Studio"
    exe.write_text(launcher())
    os.chmod(exe, 0o755)
    make_icns(ICON, app / "Contents" / "Resources" / "icon.icns")
    subprocess.run(["touch", str(app)])  # Finder refreshes the icon
    return app

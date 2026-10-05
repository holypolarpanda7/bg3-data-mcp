"""Offline checks for bg3data.iconkit (2026-10-04): DXT5 DDS headers with and without mips, atlas UVs (64 px tiles,
half-pixel inset, as dnd55e's and the Toolkit's atlases), TextureBank and metadata XML.
Run: uv run python tests/iconkit_selftest.py"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PIL import Image  # noqa: E402

from bg3data import iconkit  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    fails += not cond


with tempfile.TemporaryDirectory() as d:
    img = Image.new("RGBA", (512, 512), (200, 80, 40, 255))
    p = os.path.join(d, "a.dds")
    levels = iconkit.write_dds(img, p, mips=True)
    info = iconkit.dds_info(p)
    check(levels == 10 and info["mips"] == 10, f"512 atlas has 10 mip levels ({levels}, header {info['mips']})")
    check(info["fourcc"] == "DXT5" and info["w"] == 512, f"DXT5 512x512 ({info})")
    expect = sum(max(1, (s + 3) // 4) ** 2 * 16 for s in (512 >> i for i in range(10)))
    check(os.path.getsize(p) == 128 + expect, f"file size = header + every level ({os.path.getsize(p)} vs {128 + expect})")
    p2 = os.path.join(d, "b.DDS")
    iconkit.write_dds(Image.new("RGBA", (380, 380)), p2)
    check(iconkit.dds_info(p2)["mips"] == 1 and iconkit.dds_info(p2)["w"] == 380, "380 px tooltip icon, single level")
    sq = iconkit._square(Image.new("RGBA", (400, 300)))
    check(sq.size == (300, 300), "non-square sources are centre-cropped")

lsx = iconkit._atlas_lsx(["A", "B", "C"], 512, "Assets/Textures/Icons/X.dds", "u-1")
uv = re.findall(r'id="U1" type="float" value="([^"]+)"', lsx)
check(abs(float(uv[0]) - 0.5 / 512) < 1e-7 and abs(float(uv[1]) - 64.5 / 512) < 1e-7, f"tile UVs inset half a pixel ({uv[:2]})")
check('value="X.dds"' not in lsx and 'Assets/Textures/Icons/X.dds' in lsx and 'value="u-1"' in lsx, "atlas path + uuid")
bank = iconkit._texture_bank_lsx("X", "u-1", "Public/M/Assets/Textures/Icons/X.dds", 512)
check('region id="TextureBank"' in bank and 'value="u-1"' in bank and 'SRGB" type="bool" value="False"' in bank, "TextureBank resource")
meta = iconkit._metadata_lsx({"Assets/Tooltips/Icons/A.png": 380})
check('value="Assets/Tooltips/Icons/A.png"' in meta and 'id="w" type="int16" value="380"' in meta, "metadata entry")
print(f"{'ALL PASS' if not fails else f'{fails} FAIL'}")
sys.exit(1 if fails else 0)

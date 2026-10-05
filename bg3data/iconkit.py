"""Icon pipeline: base-game icons out as PNG, a mod's own icons in, and contact sheets to review them.

Layout copied from dnd55e's release (read 2026-10-04), the same the Toolkit's Texture Atlas Editor produces:
  Public/<mod>/GUI/<Atlas>.lsx                                IconUV list (64 px tiles, half-pixel inset) + TextureAtlasInfo
  Public/<mod>/Assets/Textures/Icons/<Atlas>.dds              DXT5, power-of-two square, full mip chain (the hotbar)
  Public/<mod>/Content/[PAK]_<Atlas>/<uuid>.lsf               TextureBank resource loading that .dds (else icons draw black)
  Mods/<mod>/GUI/Assets/Tooltips/Icons/<name>.DDS             380 px DXT5 (AssetsLowRes: 192 px)
  Mods/<mod>/GUI/Assets/ControllerUIIcons/skills_png/<name>.DDS  144 px DXT5 (AssetsLowRes: 72 px)
  Mods/<mod>/GUI/metadata.lsf                                 w/h/mipcount per "Assets/.../<name>.png" (full-size copies only)
DDS files are written with Pillow (DXT5); the atlas mip levels are encoded one by one and concatenated, so no texconv.

A source icon is any PNG (square or not: centre-cropped) named <IconName>.png; <IconName> is what stats put in `Icon`.
Files starting with _ are skipped (previews). The editable metadata list lives in <mod root>/Icons/metadata.lsx (not packed);
by convention a mod keeps its icon sources in <mod root>/Icons/src.
"""
import glob
import io
import json
import math
import os
import re
import struct
import uuid

from . import platform, sources

TILE = 64
SIZES = {  # (folder under GUI/, high-res px, low-res px)
    "tooltip": ("Tooltips/Icons", 380, 192),
    "controller": ("ControllerUIIcons/skills_png", 144, 72),
}
EXTRACT_PAKS = ("Icons.pak", "Game.pak", "Shared.pak", "Gustav.pak", "GustavX.pak")
INDEX_FILE = os.path.join(platform.cache_dir(), "icon_sources.json")


def _pil():
    try:
        from PIL import Image  # noqa: F401
        import PIL
    except ImportError as e:
        raise RuntimeError("Pillow is missing in the MCP venv: uv pip install 'pillow>=11.2'") from e
    major, minor = (int(x) for x in PIL.__version__.split(".")[:2])
    if (major, minor) < (11, 2):
        raise RuntimeError(f"Pillow {PIL.__version__} can't write DXT5 DDS; need 11.2+")
    from PIL import Image
    return Image


# ---------------------------------------------------------------- DDS
def _dxt5(img):
    """DXT5 payload (no header) of one RGBA image, plus Pillow's header."""
    buf = io.BytesIO()
    img.save(buf, format="DDS", pixel_format="DXT5")
    raw = buf.getvalue()
    return raw[:128], raw[128:]


def write_dds(img, path, mips=False):
    """RGBA image -> DXT5 .dds; mips=True adds the full chain down to 1x1 (what an atlas needs)."""
    Image = _pil()
    img = img.convert("RGBA")
    head, data = _dxt5(img)
    levels = 1
    if mips:
        w, h = img.size
        while w > 1 or h > 1:
            w, h = max(1, w // 2), max(1, h // 2)
            _, d = _dxt5(img.resize((w, h), Image.LANCZOS))
            data += d
            levels += 1
        head = bytearray(head)
        flags = struct.unpack_from("<I", head, 8)[0] | 0x20000            # DDSD_MIPMAPCOUNT
        caps = struct.unpack_from("<I", head, 108)[0] | 0x400008          # DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
        struct.pack_into("<I", head, 8, flags)
        struct.pack_into("<I", head, 28, levels)
        struct.pack_into("<I", head, 108, caps)
        head = bytes(head)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(head + data)
    return levels


def dds_info(path):
    b = open(path, "rb").read(128)
    h, w = struct.unpack_from("<II", b, 12)
    return {"w": w, "h": h, "mips": struct.unpack_from("<I", b, 28)[0] or 1, "fourcc": b[84:88].decode("latin1")}


# ---------------------------------------------------------------- generated art in
def key_green(img, low=12, high=90):
    """Green screen -> transparency (the BG3 icon LoRAs paint on green on purpose). A pixel's greenness is
    g - max(r, b): up to `low` it stays opaque, from `high` it is fully transparent, between it fades. Every pixel
    keeps g <= max(r, b) afterwards (despill), so glows don't keep a green halo."""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            m = max(r, b)
            k = g - m
            if k <= 0:
                continue
            alpha = a if k <= low else (0 if k >= high else int(a * (high - k) / (high - low)))
            px[x, y] = (r, m, b, alpha)
    return img


def import_art(paths, src_dir, names=None, key="green", size=512):
    """Copy generated images into a mod's icon sources as <IconName>.png: green keyed out, square, `size` px.
    names: one per path (default: the file name without ComfyUI's _00001_ counter)."""
    Image = _pil()
    os.makedirs(src_dir, exist_ok=True)
    out = []
    for i, p in enumerate(paths):
        name = (names[i] if names else re.sub(r"_\d+_?$", "", os.path.splitext(os.path.basename(p))[0]))
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError(f"bad icon name {name!r}")
        img = _square(Image.open(p).convert("RGBA"))
        if key == "green":
            img = key_green(img)
        img = img.resize((size, size), Image.LANCZOS)
        dst = os.path.join(src_dir, f"{name}.png")
        img.save(dst)
        out.append(dst)
    return out


# ---------------------------------------------------------------- sources
def _square(img):
    w, h = img.size
    s = min(w, h)
    return img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))


def load_sources(src_dir):
    """{IconName: RGBA square image} from <src_dir>/*.png."""
    Image = _pil()
    out = {}
    for p in sorted(glob.glob(os.path.join(src_dir, "*.png"))):
        name = os.path.splitext(os.path.basename(p))[0]
        if name.startswith("_"):  # previews and scratch files
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError(f"{p}: icon names may only use letters, digits and _ (it becomes the stats `Icon` value)")
        out[name] = _square(Image.open(p).convert("RGBA"))
    return out


# ---------------------------------------------------------------- mod layout
def mod_dirs(store, layer):
    """(mod root, folder name) of an unpacked mod layer."""
    for m in store.cfg["mods"]:
        if m["name"] == layer and not m["path"].lower().endswith(".pak"):
            metas = glob.glob(os.path.join(m["path"], "Mods", "*", "meta.lsx"))
            if not metas:
                raise RuntimeError(f"{layer}: no Mods/<folder>/meta.lsx under {m['path']}")
            return m["path"], os.path.basename(os.path.dirname(metas[0]))
    raise RuntimeError(f"{layer} isn't an unpacked mod layer (bg3_layers)")


def _atlas_lsx(names, size, rel_dds, atlas_uuid):
    per_row = size // TILE
    nodes = []
    for i, n in enumerate(names):
        c, r = i % per_row, i // per_row
        u1, u2 = (c * TILE + 0.5) / size, ((c + 1) * TILE - 0.5) / size
        v1, v2 = (r * TILE + 0.5) / size, ((r + 1) * TILE - 0.5) / size
        nodes.append(
            '                <node id="IconUV">\n'
            f'                    <attribute id="MapKey" type="FixedString" value="{n}"/>\n'
            f'                    <attribute id="U1" type="float" value="{u1:.8g}"/>\n'
            f'                    <attribute id="U2" type="float" value="{u2:.8g}"/>\n'
            f'                    <attribute id="V1" type="float" value="{v1:.8g}"/>\n'
            f'                    <attribute id="V2" type="float" value="{v2:.8g}"/>\n'
            '                </node>\n')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<save>\n    <version major="4" minor="8" revision="0" build="500"/>\n'
            '    <region id="IconUVList">\n        <node id="root">\n            <children>\n' + "".join(nodes) +
            '            </children>\n        </node>\n    </region>\n    <region id="TextureAtlasInfo">\n        <node id="root">\n'
            '            <children>\n                <node id="TextureAtlasIconSize">\n'
            f'                    <attribute id="Height" type="int32" value="{TILE}"/>\n'
            f'                    <attribute id="Width" type="int32" value="{TILE}"/>\n                </node>\n'
            '                <node id="TextureAtlasPath">\n'
            f'                    <attribute id="Path" type="string" value="{rel_dds}"/>\n'
            f'                    <attribute id="UUID" type="FixedString" value="{atlas_uuid}"/>\n                </node>\n'
            '                <node id="TextureAtlasTextureSize">\n'
            f'                    <attribute id="Height" type="int32" value="{size}"/>\n'
            f'                    <attribute id="Width" type="int32" value="{size}"/>\n                </node>\n'
            '            </children>\n        </node>\n    </region>\n</save>\n')


def _texture_bank_lsx(name, res_id, source_file, size):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<save>\n\t<version major="4" minor="8" revision="0" build="500" />\n'
            '\t<region id="TextureBank">\n\t\t<node id="TextureBank">\n\t\t\t<children>\n\t\t\t\t<node id="Resource">\n'
            f'\t\t\t\t\t<attribute id="ID" type="FixedString" value="{res_id}" />\n'
            f'\t\t\t\t\t<attribute id="Name" type="LSString" value="{name}" />\n'
            f'\t\t\t\t\t<attribute id="SourceFile" type="LSString" value="{source_file}" />\n'
            f'\t\t\t\t\t<attribute id="Template" type="FixedString" value="{name}" />\n'
            '\t\t\t\t\t<attribute id="Streaming" type="bool" value="True" />\n'
            '\t\t\t\t\t<attribute id="Type" type="int32" value="1" />\n'
            '\t\t\t\t\t<attribute id="SRGB" type="bool" value="False" />\n'
            f'\t\t\t\t\t<attribute id="Width" type="int32" value="{size}" />\n'
            f'\t\t\t\t\t<attribute id="Height" type="int32" value="{size}" />\n'
            '\t\t\t\t\t<attribute id="Depth" type="int32" value="1" />\n'
            '\t\t\t\t</node>\n\t\t\t</children>\n\t\t</node>\n\t</region>\n</save>\n')


def _metadata_lsx(entries):
    objs = "".join(
        '\t\t\t\t\t\t<node id="Object">\n'
        f'\t\t\t\t\t\t\t<attribute id="MapKey" type="FixedString" value="{key}" />\n'
        '\t\t\t\t\t\t\t<children>\n\t\t\t\t\t\t\t\t<node id="entries">\n'
        f'\t\t\t\t\t\t\t\t\t<attribute id="h" type="int16" value="{px}" />\n'
        '\t\t\t\t\t\t\t\t\t<attribute id="mipcount" type="int8" value="1" />\n'
        f'\t\t\t\t\t\t\t\t\t<attribute id="w" type="int16" value="{px}" />\n'
        '\t\t\t\t\t\t\t\t</node>\n\t\t\t\t\t\t\t</children>\n\t\t\t\t\t\t</node>\n'
        for key, px in sorted(entries.items()))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<save>\n\t<version major="4" minor="8" revision="0" build="500" />\n'
            '\t<region id="config">\n\t\t<node id="config">\n\t\t\t<children>\n\t\t\t\t<node id="entries">\n\t\t\t\t\t<children>\n'
            + objs + '\t\t\t\t\t</children>\n\t\t\t\t</node>\n\t\t\t</children>\n\t\t</node>\n\t</region>\n</save>\n')


def build(store, layer, src_dir, atlas="Icons"):
    """Write a layer's icon set from <src_dir>/*.png (see the module doc). Replaces the files this pipeline owns:
    the named atlas, and the tooltip/controller DDS + metadata entries of every icon in src_dir."""
    Image = _pil()
    icons = load_sources(src_dir)
    if not icons:
        raise RuntimeError(f"no <IconName>.png files in {src_dir}")
    root, folder = mod_dirs(store, layer)
    atlas = re.sub(r"[^A-Za-z0-9_]", "", f"{layer.capitalize()}_{atlas}")
    names = sorted(icons)
    size = 512
    while (size // TILE) ** 2 < len(names):
        size *= 2
    sheet = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    per_row = size // TILE
    for i, n in enumerate(names):
        sheet.paste(icons[n].resize((TILE, TILE), Image.LANCZOS), ((i % per_row) * TILE, (i // per_row) * TILE))
    rel_dds = f"Assets/Textures/Icons/{atlas}.dds"
    mips = write_dds(sheet, os.path.join(root, "Public", folder, *rel_dds.split("/")), mips=True)
    atlas_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bg3data-icon-atlas:{layer}:{atlas}"))
    lsx = os.path.join(root, "Public", folder, "GUI", f"{atlas}.lsx")
    os.makedirs(os.path.dirname(lsx), exist_ok=True)
    open(lsx, "w", encoding="utf-8").write(_atlas_lsx(names, size, rel_dds, atlas_uuid))
    # the TextureBank resource that loads the atlas .dds under that UUID: without it the game knows the UVs but
    # draws the icons black (seen in game 2026-10-04)
    bank_lsx = os.path.join(root, "Icons", f"{atlas}_TextureBank.lsx")
    os.makedirs(os.path.dirname(bank_lsx), exist_ok=True)
    open(bank_lsx, "w", encoding="utf-8").write(_texture_bank_lsx(atlas, atlas_uuid, f"Public/{folder}/{rel_dds}", size))
    bank_dir = os.path.join(root, "Public", folder, "Content", f"[PAK]_{atlas}")
    os.makedirs(bank_dir, exist_ok=True)
    sources.divine(store.cfg, "-a", "convert-resource", "-s", platform.to_win(bank_lsx),
                   "-d", platform.to_win(os.path.join(bank_dir, f"{atlas_uuid}.lsf")), "-i", "lsx", "-o", "lsf")

    gui = os.path.join(root, "Mods", folder, "GUI")
    meta_lsx = os.path.join(root, "Icons", "metadata.lsx")  # editable copy, outside the packed folders
    entries = {}
    if os.path.exists(meta_lsx):  # keep icons an earlier build (or the author) registered
        for key, px in re.findall(r'value="(Assets/[^"]+)" />.*?id="h" type="int16" value="(\d+)"', open(meta_lsx, encoding="utf-8").read(), re.S):
            entries[key] = int(px)
    for n in names:
        for kind, (sub, hi, lo) in SIZES.items():
            write_dds(icons[n].resize((hi, hi), Image.LANCZOS), os.path.join(gui, "Assets", *sub.split("/"), f"{n}.DDS"))
            write_dds(icons[n].resize((lo, lo), Image.LANCZOS), os.path.join(gui, "AssetsLowRes", *sub.split("/"), f"{n}.DDS"))
            entries[f"Assets/{sub}/{n}.png"] = hi
    os.makedirs(gui, exist_ok=True)
    os.makedirs(os.path.dirname(meta_lsx), exist_ok=True)
    open(meta_lsx, "w", encoding="utf-8").write(_metadata_lsx(entries))
    meta_lsf = os.path.join(gui, "metadata.lsf")
    sources.divine(store.cfg, "-a", "convert-resource", "-s", platform.to_win(meta_lsx), "-d", platform.to_win(meta_lsf),
                   "-i", "lsx", "-o", "lsf")
    return (f"{layer}: {len(names)} icons -> atlas {rel_dds} ({size}x{size}, {mips} mips, uuid {atlas_uuid}), "
            f"GUI/{atlas}.lsx, tooltip 380/192 + controller 144/72 DDS each, metadata.lsf ({len(entries)} entries)\n"
            f"  icons: {', '.join(names[:20])}{' ...' if len(names) > 20 else ''}\n"
            "  Use the names as stats `Icon` values; repack/deploy, then bg3_icon_check to confirm the game draws them.")


# ---------------------------------------------------------------- base-game icons out
def _index(cfg):
    """{icon name: (pak, path)} of the base game's 380 px tooltip icons (cached)."""
    try:
        idx = json.load(open(INDEX_FILE))
        if idx.get("v") == 1:
            return idx["icons"]
    except (OSError, ValueError):
        pass
    data = cfg["base"]["game_data"]
    icons = {}
    for pak in EXTRACT_PAKS:
        p = os.path.join(data, pak)
        if not os.path.exists(p):
            continue
        for line in str(sources.divine(cfg, "-a", "list-package", "-s", platform.to_win(p))).splitlines():
            f = line.split("\t")[0]
            m = re.search(r"/Assets/Tooltips/Icons/([^/]+)\.DDS$", f, re.I)
            if m and "AssetsLowRes" not in f:
                icons.setdefault(m.group(1), (pak, f))
    os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
    json.dump({"v": 1, "icons": icons}, open(INDEX_FILE, "w"))
    return icons


def extract(store, names, out_dir):
    """Base-game icons (380 px tooltip versions) as PNGs in out_dir; names may be icon names or substrings."""
    Image = _pil()
    idx = _index(store.cfg)
    data_dir = store.cfg["base"]["game_data"]
    os.makedirs(out_dir, exist_ok=True)
    out, missing = [], []
    for q in names:
        hits = [q] if q in idx else sorted(k for k in idx if q.lower() in k.lower())[:12]
        if not hits:
            missing.append(q)
            continue
        for n in hits:
            pak, path = idx[n]
            tmp = os.path.join(out_dir, f"{n}.DDS")
            sources.divine(store.cfg, "-a", "extract-single-file", "-s", platform.to_win(os.path.join(data_dir, pak)),
                           "-d", platform.to_win(tmp), "-f", path)
            png = os.path.join(out_dir, f"{n}.png")
            Image.open(tmp).convert("RGBA").save(png)
            os.remove(tmp)
            out.append(png)
    return out, missing, len(idx)


def preview(src_dir, out_png=None, size=128, cols=8):
    """Contact sheet of <src_dir>/*.png (name under each tile) to review a batch."""
    Image = _pil()
    from PIL import ImageDraw
    icons = load_sources(src_dir)
    if not icons:
        raise RuntimeError(f"no PNGs in {src_dir}")
    rows = math.ceil(len(icons) / cols)
    sheet = Image.new("RGBA", (cols * (size + 8), rows * (size + 24)), (24, 22, 20, 255))
    d = ImageDraw.Draw(sheet)
    for i, (n, img) in enumerate(sorted(icons.items())):
        x, y = (i % cols) * (size + 8) + 4, (i // cols) * (size + 24) + 4
        sheet.paste(img.resize((size, size), Image.LANCZOS), (x, y))
        d.text((x, y + size + 2), n[:22], fill=(220, 210, 190, 255))
    out_png = out_png or os.path.join(src_dir, "_preview.png")
    sheet.save(out_png)
    return out_png, len(icons)

"""Profile & rank cards — wide rounded PNG cards with avatar, name, a stat grid, a progress bar and badges.

Pure Pillow, no network access: callers pass avatar / background bytes in.  Fonts (Outfit, Work Sans — OFL) are
embedded in bsg_fonts.py and unpacked to data/fonts on first use.

    render_profile_card({"username", "avatar_bytes", "bg_bytes", "accent_color", "title", "statline": [(label, value)],
                         "stats": [(label, value, sub)] (up to 8), "badge_images": [bytes], "badge_total": int})
    render_rank_card({"username", "avatar_bytes", "bg_bytes", "accent_color", "label", "level", "xp_in_level",
                      "xp_for_level", "xp_to_next", "position", "total", "stats": [(label, value, sub)] (up to 4),
                      "badge_images", "badge_total"})
"""

from __future__ import annotations

import io
import math
import re
from functools import lru_cache
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageChops


# ============================================================================
# Layout constants
# ============================================================================

# Public card size (final PNG dimensions)
CARD_W, CARD_H = 1300, 500

# Internal render scale — everything is drawn at 2x and downsampled at the
# end for crisp edges/text without needing per-shape anti-aliasing tricks.
_SCALE = 2
_W, _H = CARD_W * _SCALE, CARD_H * _SCALE

_RADIUS  = 28 * _SCALE   # outer card corner radius
_BORDER  = 4  * _SCALE   # outer accent border width
_MARGIN  = 36 * _SCALE   # standard inner margin

_AVATAR_D = 132 * _SCALE  # avatar diameter
_AVATAR_X = _MARGIN
_AVATAR_Y = _MARGIN

_HEADER_TEXT_X = _AVATAR_X + _AVATAR_D + 28 * _SCALE

_DIVIDER_Y = 192 * _SCALE

_BADGE_SIZE = 40 * _SCALE
_BADGE_GAP  = 10 * _SCALE
_BADGE_ROW_Y = 448 * _SCALE
_BADGE_LABEL_Y = 424 * _SCALE
_BADGE_MAX_SHOWN = 14

# Stat grid (4 columns)
_GRID_COLS = 4
_GRID_GAP  = 16 * _SCALE
_GRID_Y0   = 210 * _SCALE
_GRID_ROW_H = 96 * _SCALE
_GRID_X0   = _MARGIN
_GRID_X1   = _W - _MARGIN
_GRID_CELL_W = (_GRID_X1 - _GRID_X0 - (_GRID_COLS - 1) * _GRID_GAP) // _GRID_COLS


# ============================================================================
# Palette
# ============================================================================

BG_BASE_TOP    = (32, 36, 56)     # default-background gradient top-left
BG_BASE_BOTTOM = (17, 19, 28)     # default-background gradient bottom-right
PANEL_FILL     = (18, 20, 32, 150)   # translucent stat-cell panel
PANEL_STROKE   = (255, 255, 255, 18)
TEXT_PRIMARY   = (244, 242, 236)
TEXT_MUTED     = (162, 172, 199)
TEXT_DIM       = (120, 129, 153)
TRACK_COLOR    = (255, 255, 255, 35)


def _accent(color) -> tuple[int, int, int]:
    """Normalize an accent color to an (r, g, b) tuple.

    Accepts an int (0xRRGGBB), a hex string, or an (r,g,b) tuple.
    """
    if isinstance(color, tuple) and len(color) >= 3:
        return tuple(int(c) for c in color[:3])
    if isinstance(color, str):
        color = color.lstrip("#")
        color = int(color, 16)
    color = int(color)
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255)


# ============================================================================
# Fonts
# ============================================================================

import os

_WEIGHTS = {"display": ("Outfit", 700), "title": ("Outfit", 400), "bold": ("WorkSans", 700), "body": ("WorkSans", 400)}


def _font_dir() -> str:
    try:
        import bsg_fonts
        return bsg_fonts.ensure()
    except Exception:
        return ""


@lru_cache(maxsize=128)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    family, weight = _WEIGHTS.get(kind, _WEIGHTS["body"])
    d = _font_dir()
    try:
        f = ImageFont.truetype(os.path.join(d, family + ".ttf"), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f
    except Exception:
        try:
            return ImageFont.load_default(size)        # Pillow >= 10.1: scalable fallback
        except Exception:
            return ImageFont.load_default()


# ============================================================================
# Small drawing helpers
# ============================================================================

def _spaced_width(text: str, font: ImageFont.FreeTypeFont, spacing: float) -> float:
    if not text:
        return 0.0
    return sum(font.getlength(ch) for ch in text) + spacing * (len(text) - 1)


def _draw_spaced(draw: ImageDraw.ImageDraw, xy, text: str,
                  font: ImageFont.FreeTypeFont, fill, spacing: float = 0.0) -> float:
    """Draw text with manual per-character letter-spacing. Returns end x."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + spacing
    return x


def _fit_spaced_font(text: str, kind: str, start_size: int, min_size: int,
                      max_width: float, spacing_ratio: float = 0.12):
    """Shrink font size until `text` (with letter-spacing) fits max_width.

    Returns (font, spacing_px, text). If even min_size overflows, the text
    is truncated with an ellipsis.
    """
    size = start_size
    while size > min_size:
        font = _font(kind, size)
        spacing = size * spacing_ratio
        if _spaced_width(text, font, spacing) <= max_width:
            return font, spacing, text
        size -= 2
    font = _font(kind, min_size)
    spacing = min_size * spacing_ratio
    out = text
    while out and _spaced_width(out + "…", font, spacing) > max_width:
        out = out[:-1]
    if out != text:
        out += "…"
    return font, spacing, out


def _fit_text(text: str, kind: str, start_size: int, min_size: int, max_width: float):
    """Shrink (no letter spacing) font size until `text` fits max_width."""
    size = start_size
    while size > min_size:
        font = _font(kind, size)
        if font.getlength(text) <= max_width:
            return font, text
        size -= 2
    font = _font(kind, min_size)
    out = text
    while out and font.getlength(out + "…") > max_width:
        out = out[:-1]
    if out != text:
        out += "…"
    return font, out


def _ellipsize(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    if font.getlength(text) <= max_width:
        return text
    out = text
    while out and font.getlength(out + "…") > max_width:
        out = out[:-1]
    return (out + "…") if out else "…"


# Bundled fonts cover Latin/Cyrillic/Greek + common punctuation, but not
# color emoji. Strip emoji / variation-selector / ZWJ ranges so unsupported
# glyphs don't render as "missing glyph" boxes on the card.
_EMOJI_RE = re.compile(
    "[" 
    "\U0001F000-\U0001FAFF"   # emoji & symbols (incl. supplemental)
    "\U00002190-\U000021FF"   # arrows
    "\U00002300-\U000023FF"   # misc technical (incl. ⏰ etc.)
    "\U00002460-\U000027BF"   # enclosed alnum, dingbats
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0000200D"              # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def _clean_text(text: str) -> str:
    if not text:
        return text
    return _EMOJI_RE.sub("", text)


def _open_image(data: Optional[bytes]) -> Optional[Image.Image]:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)  # first frame, in case of GIF/APNG
        return img.convert("RGBA")
    except Exception:
        return None


def _horizontal_gradient_mask(w: int, h: int, start: int, end: int) -> Image.Image:
    """An 'L' image, w x h, with a horizontal gradient from `start` to `end`."""
    row = Image.new("L", (w, 1))
    px = row.load()
    for x in range(w):
        t = x / max(1, w - 1)
        px[x, 0] = int(start + (end - start) * t)
    return row.resize((w, h))


def _round_rect_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    return mask


def _circle_mask(d: int) -> Image.Image:
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    return mask


def _fit_cover(img: Image.Image, size: int) -> Image.Image:
    """Resize+crop `img` to fill a `size` x `size` square (cover fit)."""
    return ImageOps.fit(img, (size, size), method=Image.LANCZOS)


def _circular_image(data: Optional[bytes], size: int, fallback_rgb=(70, 76, 102)) -> Image.Image:
    """Return an RGBA size x size circular image from `data`, or a plain
    filled circle if `data` couldn't be decoded."""
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img = _open_image(data)
    if img is not None:
        img = _fit_cover(img, size)
    else:
        img = Image.new("RGBA", (size, size), (*fallback_rgb, 255))
    mask = _circle_mask(size)
    out.paste(img, (0, 0), mask)
    return out


def _paste_ring(base: Image.Image, xy, size: int, ring_color, ring_w: int):
    """Draw a circular ring outline centered on the (already pasted) image at xy."""
    d = ImageDraw.Draw(base)
    x0, y0 = xy
    d.ellipse((x0, y0, x0 + size, y0 + size), outline=ring_color, width=ring_w)


# ============================================================================
# Backgrounds
# ============================================================================

def _wavy_overlay(w: int, h: int, color=(255, 255, 255, 14), count: int = 9) -> Image.Image:
    """Subtle horizontal wave-line texture, drawn on a transparent layer."""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = max(1, w // 140)
    for i in range(count):
        base_y = (i + 0.5) * h / count
        amp = h / count * 0.42
        pts = []
        for x in range(0, w + step, step):
            y = base_y + math.sin((x / w) * 4 * math.pi + i * 0.9) * amp
            pts.append((x, y))
        d.line(pts, fill=color, width=max(1, 2 * _SCALE))
    return layer


def _default_background(w: int, h: int) -> Image.Image:
    """Generated dark navy background w/ diagonal gradient + wave texture,
    used when the guild hasn't uploaded a custom card background."""
    # Build the diagonal gradient from a 1D horizontal row + 1D vertical
    # column (each resized up), instead of a w*h Python pixel loop.
    h_row = Image.new("L", (w, 1))
    h_px = h_row.load()
    for x in range(w):
        h_px[x, 0] = int(255 * (1 - x / max(1, w - 1)))
    h_grad = h_row.resize((w, h))

    v_col = Image.new("L", (1, h))
    v_px = v_col.load()
    for y in range(h):
        v_px[0, y] = int(255 * (1 - y / max(1, h - 1)))
    v_grad = v_col.resize((w, h))

    diag = ImageChops.add(h_grad.point(lambda p: p // 2), v_grad.point(lambda p: p // 2))

    top = Image.new("RGB", (w, h), BG_BASE_TOP)
    bottom = Image.new("RGB", (w, h), BG_BASE_BOTTOM)
    bg = Image.composite(top, bottom, diag)
    bg = bg.convert("RGBA")
    bg.alpha_composite(_wavy_overlay(w, h))
    return bg


def _prepare_background(bg_bytes: Optional[bytes], w: int, h: int) -> Image.Image:
    """Cover-fit a guild-supplied background image to w x h, or fall back
    to the generated default."""
    img = _open_image(bg_bytes)
    if img is None:
        return _default_background(w, h)
    try:
        return ImageOps.fit(img.convert("RGB"), (w, h), method=Image.LANCZOS).convert("RGBA")
    except Exception:
        return _default_background(w, h)


def _apply_scrim(bg: Image.Image) -> Image.Image:
    """Darken the left ~70% of the card so header/stat text stays legible
    over arbitrary background art, while the right edge stays clearer."""
    w, h = bg.size
    mask = _horizontal_gradient_mask(w, h, start=235, end=30)
    # Push the falloff earlier so the dark zone covers more of the left side.
    mask = mask.point(lambda p: min(255, int(p * 1.35)))
    overlay = Image.new("RGB", (w, h), (10, 12, 20))
    bg = bg.convert("RGB")
    bg.paste(overlay, (0, 0), mask)
    return bg.convert("RGBA")


def _finalize_card(canvas: Image.Image, accent_rgb) -> Image.Image:
    """Round the corners, draw the accent border, downsample to CARD_W x CARD_H."""
    mask = _round_rect_mask(_W, _H, _RADIUS)
    rounded = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    rounded.paste(canvas, (0, 0), mask)

    d = ImageDraw.Draw(rounded)
    inset = _BORDER // 2
    d.rounded_rectangle(
        (inset, inset, _W - 1 - inset, _H - 1 - inset),
        radius=_RADIUS, outline=(*accent_rgb, 255), width=_BORDER,
    )
    return rounded.resize((CARD_W, CARD_H), Image.LANCZOS)


# ============================================================================
# Header / shared chrome
# ============================================================================

def _draw_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, overlay: ImageDraw.ImageDraw, *,
                 avatar_bytes: Optional[bytes], username: str, accent_rgb,
                 subtitle: str, subtitle_color, statline_segments: Sequence[tuple[str, tuple]]):
    """Draw the avatar + decorative accents + name + subtitle + stat line.

    `overlay` is a draw context for a transparent full-card layer that gets
    alpha-composited onto `canvas` afterwards — used for any translucent
    decorations so they blend correctly with whatever is beneath them.

    `statline_segments` is a list of (text, rgb) pairs drawn left-to-right
    on one line, e.g. [("Score ", muted), ("+340", white), ("   |   ", muted), ...].
    Returns the y-coordinate of the divider line.
    """
    # Avatar with ring (opaque — drawn directly on canvas)
    avatar = _circular_image(avatar_bytes, _AVATAR_D)
    canvas.paste(avatar, (_AVATAR_X, _AVATAR_Y), avatar)
    _paste_ring(canvas, (_AVATAR_X, _AVATAR_Y), _AVATAR_D, (*accent_rgb, 255), max(1, 3 * _SCALE))

    # Decorative diamond accents near the avatar (echoes the reference card)
    cx0, cy0 = _AVATAR_X + _AVATAR_D, _AVATAR_Y
    cx1, cy1 = _AVATAR_X, _AVATAR_Y + _AVATAR_D
    _diamond(overlay, (cx0 - 2 * _SCALE, cy0 + 14 * _SCALE), 9 * _SCALE, (255, 255, 255, 200))
    _diamond(overlay, (cx0 + 16 * _SCALE, cy0 - 4 * _SCALE), 5 * _SCALE, (255, 255, 255, 120))
    _diamond(overlay, (cx1 + 16 * _SCALE, cy1 - 6 * _SCALE), 7 * _SCALE, (255, 255, 255, 150))

    text_x = _HEADER_TEXT_X
    max_w = _W - text_x - _MARGIN

    # Username — bold, spaced caps
    name_font, spacing, name_text = _fit_spaced_font(
        _clean_text(username).upper() or "UNKNOWN", "display", 46 * _SCALE, 24 * _SCALE, max_w, spacing_ratio=0.16)
    _draw_spaced(draw, (text_x, 38 * _SCALE), name_text, name_font, TEXT_PRIMARY, spacing)

    # Subtitle
    sub_font, sub_text = _fit_text(_clean_text(subtitle), "title", 26 * _SCALE, 16 * _SCALE, max_w)
    draw.text((text_x, 102 * _SCALE), sub_text, font=sub_font, fill=subtitle_color)

    # Stat line
    body_font = _font("body", 20 * _SCALE)
    bold_font = _font("bold", 20 * _SCALE)
    sx = text_x
    sy = 146 * _SCALE
    for text, color in statline_segments:
        f = bold_font if color == TEXT_PRIMARY else body_font
        text = _clean_text(text)
        draw.text((sx, sy), text, font=f, fill=color)
        sx += f.getlength(text)
        if sx > _W - _MARGIN:
            break

    # Divider (translucent — drawn on the overlay layer)
    overlay.line((_MARGIN, _DIVIDER_Y, _W - _MARGIN, _DIVIDER_Y),
                  fill=(255, 255, 255, 40), width=max(1, _SCALE))
    return _DIVIDER_Y


def _diamond(draw: ImageDraw.ImageDraw, center, r, fill):
    x, y = center
    draw.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=fill)


# ============================================================================
# Stat grid
# ============================================================================

def _stat_cell(canvas: Image.Image, draw: ImageDraw.ImageDraw, col: int, row: int,
               label: str, value: str, sub: str = "", value_color=TEXT_PRIMARY):
    x0 = _GRID_X0 + col * (_GRID_CELL_W + _GRID_GAP)
    y0 = _GRID_Y0 + row * (_GRID_ROW_H + _GRID_GAP)
    x1 = x0 + _GRID_CELL_W
    y1 = y0 + _GRID_ROW_H

    panel = Image.new("RGBA", (_GRID_CELL_W, _GRID_ROW_H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((0, 0, _GRID_CELL_W - 1, _GRID_ROW_H - 1),
                          radius=14 * _SCALE, fill=PANEL_FILL, outline=PANEL_STROKE,
                          width=max(1, _SCALE))
    canvas.alpha_composite(panel, (x0, y0))

    pad = 16 * _SCALE
    label_font = _font("bold", 14 * _SCALE)
    draw.text((x0 + pad, y0 + 12 * _SCALE), _clean_text(label).upper(), font=label_font, fill=TEXT_DIM)

    val_font, val_text = _fit_text(_clean_text(value) or "—", "display", 28 * _SCALE, 18 * _SCALE,
                                    _GRID_CELL_W - pad * 2)
    draw.text((x0 + pad, y0 + 36 * _SCALE), val_text, font=val_font, fill=value_color)

    if sub:
        sub_font = _font("body", 15 * _SCALE)
        sub_text = _ellipsize(_clean_text(sub), sub_font, _GRID_CELL_W - pad * 2)
        draw.text((x0 + pad, y0 + _GRID_ROW_H - 26 * _SCALE), sub_text,
                  font=sub_font, fill=TEXT_MUTED)


# ============================================================================
# Badge row
# ============================================================================

def _draw_badge_row(canvas: Image.Image, draw: ImageDraw.ImageDraw, overlay: ImageDraw.ImageDraw,
                     badge_images: Sequence[bytes], total_count: int, accent_rgb):
    label = f"BADGES ({total_count})" if total_count else "BADGES"
    draw.text((_MARGIN, _BADGE_LABEL_Y), label, font=_font("bold", 15 * _SCALE), fill=TEXT_DIM)

    if not badge_images:
        draw.text((_MARGIN, _BADGE_ROW_Y + 6 * _SCALE), "No badges earned yet",
                  font=_font("body", 16 * _SCALE), fill=TEXT_MUTED)
        return

    x = _MARGIN
    shown = list(badge_images[:_BADGE_MAX_SHOWN])
    for raw in shown:
        icon = _circular_image(raw, _BADGE_SIZE, fallback_rgb=(60, 64, 86))
        canvas.paste(icon, (x, _BADGE_ROW_Y), icon)
        _paste_ring(canvas, (x, _BADGE_ROW_Y), _BADGE_SIZE, (255, 255, 255, 255), max(1, _SCALE))
        x += _BADGE_SIZE + _BADGE_GAP

    overflow = total_count - len(shown)
    if overflow > 0:
        font = _font("bold", 16 * _SCALE)
        d_ = _BADGE_SIZE
        cx, cy = x + d_ // 2, _BADGE_ROW_Y + d_ // 2
        overlay.ellipse((x, _BADGE_ROW_Y, x + d_, _BADGE_ROW_Y + d_),
                         fill=(255, 255, 255, 30), outline=(255, 255, 255, 120), width=max(1, _SCALE))
        text = f"+{overflow}"
        tw = font.getlength(text)
        draw.text((cx - tw / 2, cy - 12 * _SCALE), text, font=font, fill=TEXT_PRIMARY)


# ============================================================================
# Progress bar (rank card)
# ============================================================================

def _draw_progress_bar(canvas: Image.Image, draw: ImageDraw.ImageDraw, *,
                        x: int, y: int, w: int, h: int, frac: float, accent_rgb,
                        label_left: str, label_right: str):
    frac = max(0.0, min(1.0, frac))
    radius = h // 2

    track = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(track)
    td.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=TRACK_COLOR)
    canvas.alpha_composite(track, (x, y))

    fill_w = max(h, int(w * frac))
    fill = Image.new("RGBA", (fill_w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill)
    fd.rounded_rectangle((0, 0, fill_w - 1, h - 1), radius=radius, fill=(*accent_rgb, 235))
    canvas.alpha_composite(fill, (x, y))

    label_font = _font("bold", 18 * _SCALE)
    label_left = _clean_text(label_left)
    label_right = _clean_text(label_right)
    draw.text((x, y - 30 * _SCALE), label_left, font=label_font, fill=TEXT_PRIMARY)
    rt_w = label_font.getlength(label_right)
    draw.text((x + w - rt_w, y - 30 * _SCALE), label_right, font=label_font, fill=TEXT_MUTED)


# ============================================================================
# Public renderers
# ============================================================================

def _cells(canvas, draw, stats, rows_from=0, cols=4):
    for i, s in enumerate(list(stats)[:cols * (2 - rows_from)]):
        label, value, sub = (list(s) + ["", ""])[:3]
        _stat_cell(canvas, draw, i % cols, rows_from + i // cols, str(label), str(value), str(sub))


def _png(canvas, accent_rgb) -> bytes:
    out = _finalize_card(canvas, accent_rgb)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def _start(data: dict):
    bg = _apply_scrim(_prepare_background(data.get("bg_bytes"), _W, _H))
    canvas = bg.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    overlay_img = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    return canvas, draw, overlay_img, ImageDraw.Draw(overlay_img)


def render_profile_card(data: dict) -> bytes:
    """Profile card: header (name, title, status line), up to 8 stat cells, badge row."""
    accent_rgb = _accent(data.get("accent_color", 0x7C3AED))
    canvas, draw, overlay_img, overlay = _start(data)
    statline = []
    for n, (label, value) in enumerate(data.get("statline") or []):
        statline += [((("    " if n else "") + f"{label}  "), TEXT_MUTED), (str(value), TEXT_PRIMARY)]
    _draw_header(canvas, draw, overlay, avatar_bytes=data.get("avatar_bytes"), username=data.get("username", "Unknown"),
                 accent_rgb=accent_rgb, subtitle=data.get("title", ""),
                 subtitle_color=tuple(min(255, c + 60) for c in accent_rgb), statline_segments=statline)
    _cells(canvas, draw, data.get("stats") or [], 0, 4)
    _draw_badge_row(canvas, draw, overlay, data.get("badge_images") or [], data.get("badge_total", 0), accent_rgb)
    canvas.alpha_composite(overlay_img)
    return _png(canvas, accent_rgb)


def render_rank_card(data: dict) -> bytes:
    """Rank card: header, a level progress bar, up to 4 stat cells, badge row."""
    accent_rgb = _accent(data.get("accent_color", 0x7C3AED))
    canvas, draw, overlay_img, overlay = _start(data)
    pos = f"#{data['position']} of {data.get('total', 0)}" if data.get("position") else "Unranked"
    _draw_header(canvas, draw, overlay, avatar_bytes=data.get("avatar_bytes"), username=data.get("username", "Unknown"),
                 accent_rgb=accent_rgb, subtitle=f"{data.get('label', 'Rank')} — Level {data.get('level', 0)}",
                 subtitle_color=tuple(min(255, c + 60) for c in accent_rgb),
                 statline_segments=[("Server position  ", TEXT_MUTED), (pos, TEXT_PRIMARY)])
    bar_x, bar_y, bar_w, bar_h = _MARGIN, 256 * _SCALE, _W - 2 * _MARGIN, 28 * _SCALE
    per = max(1, int(data.get("xp_for_level", 1)))
    cur = max(0, int(data.get("xp_in_level", 0)))
    _draw_progress_bar(canvas, draw, x=bar_x, y=bar_y, w=bar_w, h=bar_h, frac=min(1.0, cur / per), accent_rgb=accent_rgb,
                       label_left=f"Next: Level {int(data.get('level', 0)) + 1}", label_right=f"{cur:,} / {per:,} XP")
    draw.text((bar_x, bar_y + bar_h + 10 * _SCALE), f"{int(data.get('xp_to_next', 0)):,} XP to next level",
              font=_font("body", 17 * _SCALE), fill=TEXT_MUTED)
    _cells(canvas, draw, data.get("stats") or [], 1, 4)
    _draw_badge_row(canvas, draw, overlay, data.get("badge_images") or [], data.get("badge_total", 0), accent_rgb)
    canvas.alpha_composite(overlay_img)
    return _png(canvas, accent_rgb)


# ============================================================================
# COG ENTRY POINT (Bot Forge Studio loads this file as an extension)
# ============================================================================
import asyncio  # noqa: E402
import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import commands  # noqa: E402

import bs_runtime as bs  # noqa: E402
from bs_game import host  # noqa: E402

PROFILE_COMMAND = %%profile_command%%
RANK_COMMAND = %%rank_command%%
XP_TABLE = %%xp_table%%
ACCENT = int(%%accent%%, 16)
host.wallet = %%wallet%%
host.currency_name = %%currency_name%%
host.currency_emoji = %%currency_emoji%%


def level_for(xp: float) -> int:
    lvl = 0
    while xp >= 100 * (lvl + 1) ** 2 // 2:
        lvl += 1
    return lvl


def xp_floor(level: int) -> int:
    return 100 * level ** 2 // 2 if level > 0 else 0


async def _read(asset: Optional[discord.Asset]) -> Optional[bytes]:
    if asset is None:
        return None
    try:
        return await asset.replace(size=256, format="png").read()
    except discord.HTTPException:
        return None


async def _gather(member: discord.Member):
    gid = member.guild.id
    xp = float(await bs.kv_get(XP_TABLE, bs.eco_key(gid, member.id), 0) or 0)
    lvl = level_for(xp)
    bal = await host.balance(gid, member.id)
    team = await host.get_team(gid, member.id)
    gear = await bs.service("gear.stats", gid, member.id)
    rows = [(k, v) for k, v in await bs.kv_top(XP_TABLE, 5000) if k.startswith(f"{gid}:")]
    pos = next((i for i, (k, _v) in enumerate(rows, 1) if k.endswith(f":{member.id}")), None)
    return xp, lvl, bal, team, gear, pos, len(rows)


class CardsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cmds: list = []

    async def cog_load(self):
        @app_commands.command(name=PROFILE_COMMAND, description="Show a profile card")
        @app_commands.guild_only()
        async def profile(interaction: discord.Interaction, member: Optional[discord.Member] = None):
            m = member or interaction.user
            await interaction.response.defer(thinking=True)
            xp, lvl, bal, team, gear, pos, total = await _gather(m)
            stats = [("Level", str(lvl), f"{int(xp):,} XP"), (host.currency_name.title(), f"{bal:,}", ""),
                     ("Team", team or "—", ""), ("Rank", f"#{pos}" if pos else "—", f"of {total}" if total else "")]
            if gear:
                stats += [("Attack", f"{gear['atk']:.0f}", ""), ("Health", f"{gear['hp']:.0f}", ""),
                          ("Block", f"{gear['block']:.0f}%", ""), ("Spells", str(gear["spell_charges"]), "charges")]
            png = await asyncio.to_thread(render_profile_card, {
                "username": m.display_name, "avatar_bytes": await _read(m.display_avatar), "accent_color": ACCENT,
                "title": team or m.guild.name, "statline": [("Level", lvl), ("Rank", f"#{pos}" if pos else "—")], "stats": stats})
            await interaction.followup.send(file=discord.File(io.BytesIO(png), "profile.png"))

        @app_commands.command(name=RANK_COMMAND, description="Show a rank card")
        @app_commands.guild_only()
        async def rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
            m = member or interaction.user
            await interaction.response.defer(thinking=True)
            xp, lvl, bal, team, gear, pos, total = await _gather(m)
            lo, hi = xp_floor(lvl), xp_floor(lvl + 1)
            png = await asyncio.to_thread(render_rank_card, {
                "username": m.display_name, "avatar_bytes": await _read(m.display_avatar), "accent_color": ACCENT,
                "label": "Rank", "level": lvl, "xp_in_level": xp - lo, "xp_for_level": hi - lo, "xp_to_next": hi - xp,
                "position": pos, "total": total,
                "stats": [("Total XP", f"{int(xp):,}", ""), (host.currency_name.title(), f"{bal:,}", ""),
                          ("Team", team or "—", ""), ("Level", str(lvl), "")]})
            await interaction.followup.send(file=discord.File(io.BytesIO(png), "rank.png"))

        for c in (profile, rank):
            self.bot.tree.add_command(c)
        self._cmds = [PROFILE_COMMAND, RANK_COMMAND]

    async def cog_unload(self):
        for n in self._cmds:
            self.bot.tree.remove_command(n)
        await host.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(CardsCog(bot))

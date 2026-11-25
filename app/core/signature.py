"""Electronic signature creation & adaptive paper background removal."""

from __future__ import annotations

import io
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def extract_signature_ink_bytes(
    image_bytes: bytes,
    remove_bg: bool = True,
    threshold: int = 215,
) -> bytes:
    """
    Extract crisp signature ink from a scanned/photographed paper signature.
    Samples border pixels to estimate paper luminance, strips paper background
    to alpha=0, and enhances blue or dark ink with smooth antialiasing.
    """
    im = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    if not remove_bg:
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()

    arr = np.array(im, dtype=np.float32)
    h, w, _ = arr.shape
    border_t = max(2, int(min(w, h) * 0.08))

    # Perimeter mask for adaptive paper color estimation
    mask = np.zeros((h, w), dtype=bool)
    mask[:border_t, :] = True
    mask[-border_t:, :] = True
    mask[:, :border_t] = True
    mask[:, -border_t:] = True

    border_pixels = arr[mask][:, :3]
    bg_rgb = border_pixels.mean(axis=0) if len(border_pixels) else np.array([255.0, 255.0, 255.0])
    bg_lum = 0.299 * bg_rgb[0] + 0.587 * bg_rgb[1] + 0.114 * bg_rgb[2]

    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    min_lum = float(lum.min()) if lum.size else 0.0

    sensitivity = max(0.1, min(0.95, (threshold - 130.0) / 120.0))
    contrast_range = max(15.0, bg_lum - min_lum)
    cutoff = bg_lum - (contrast_range * (0.04 + (1.0 - sensitivity) * 0.42))
    color_tol = 25.0 * (sensitivity + 0.4)

    color_dist = np.sqrt((r - bg_rgb[0]) ** 2 + (g - bg_rgb[1]) ** 2 + (b - bg_rgb[2]) ** 2)
    is_paper = (lum >= cutoff) | (color_dist < color_tol)

    alpha = np.zeros((h, w), dtype=np.uint8)
    ink_mask = ~is_paper
    if np.any(ink_mask):
        delta = np.clip(cutoff - lum[ink_mask], 0.0, None)
        alpha_ratio = delta / max(10.0, cutoff - min_lum)
        alpha[ink_mask] = np.clip(np.round(255.0 * np.power(alpha_ratio, 0.75)), 0, 255).astype(np.uint8)

        is_blue = ink_mask & (b > r + 15.0) & (b > g + 10.0)
        is_dark = ink_mask & (~is_blue)

        arr[is_blue, 0] = np.minimum(25.0, r[is_blue])
        arr[is_blue, 1] = np.minimum(50.0, g[is_blue])
        arr[is_blue, 2] = np.maximum(120.0, b[is_blue])

        arr[is_dark, 0] = np.clip(r[is_dark] - 60.0, 0.0, 20.0)
        arr[is_dark, 1] = np.clip(g[is_dark] - 60.0, 0.0, 20.0)
        arr[is_dark, 2] = np.clip(b[is_dark] - 60.0, 0.0, 20.0)

    out_arr = np.dstack([np.clip(arr[:, :, :3], 0, 255).astype(np.uint8), alpha])
    out_im = Image.fromarray(out_arr, mode="RGBA")
    buf = io.BytesIO()
    out_im.save(buf, format="PNG")
    return buf.getvalue()


def render_typed_signature_png(name: str, color_hex: str = "#000000") -> bytes:
    """Render a cursive/italic typed signature onto a transparent PNG."""
    text = (name or "Signature").strip() or "Signature"
    w, h = 480, 140
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    font = None
    for candidate in (
        r"C:\Windows\Fonts\segoesc.ttf",
        r"C:\Windows\Fonts\SCRIPTBL.TTF",
        r"C:\Windows\Fonts\BRUSHSCI.TTF",
        r"C:\Windows\Fonts\georgiai.ttf",
        r"C:\Windows\Fonts\timesi.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 54)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    hex_clean = color_hex.lstrip("#")
    if len(hex_clean) == 6:
        rgb = (int(hex_clean[0:2], 16), int(hex_clean[2:4], 16), int(hex_clean[4:6], 16), 255)
    else:
        rgb = (0, 0, 0, 255)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = max(12, (w - tw) // 2)
    y = max(8, (h - th) // 2 - bbox[1])
    draw.text((x, y), text, fill=rgb, font=font)

    # Crop tightly around non-transparent pixels
    alpha_bbox = im.getbbox()
    if alpha_bbox:
        pad = 12
        crop_box = (
            max(0, alpha_bbox[0] - pad),
            max(0, alpha_bbox[1] - pad),
            min(w, alpha_bbox[2] + pad),
            min(h, alpha_bbox[3] + pad),
        )
        im = im.crop(crop_box)

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()

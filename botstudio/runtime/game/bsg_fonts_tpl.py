"""Embedded OFL fonts (Outfit, Work Sans) for the profile / rank cards; unpacked to data/fonts on first use."""
import base64
import os
from pathlib import Path

_FONTS = {"Outfit.ttf": %%OUTFIT%%, "WorkSans.ttf": %%WORKSANS%%}


def ensure() -> str:
    d = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent / "data")) / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    for name, blob in _FONTS.items():
        p = d / name
        if not p.exists() or p.stat().st_size == 0:
            p.write_bytes(base64.b64decode(blob))
    return str(d)

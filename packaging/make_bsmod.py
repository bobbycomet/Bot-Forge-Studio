#!/usr/bin/env python3
"""Package an existing bot folder as a .bsmod node bundle.

  Cog-style (has setup(bot)):   make_bsmod.py ./mycog --id mycog --title "My Cog" --entry cogs/mycog.py
  Whole script bot (own .run):  make_bsmod.py ./mybot --id mybot --title "My Bot" --standalone main.py \
                                    --requires aiosqlite aiohttp --env DATABASE_URL "" "Optional Postgres URL"
Drop the result in ~/.config/botstudio/plugins/ and it appears under Module in the palette.
"""
import argparse, json, zipfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("folder"); ap.add_argument("--id", required=True); ap.add_argument("--title", required=True)
ap.add_argument("--entry"); ap.add_argument("--standalone")
ap.add_argument("--description", default=""); ap.add_argument("--requires", nargs="*", default=[])
ap.add_argument("--intents", nargs="*", default=[])
ap.add_argument("--env", nargs=3, action="append", default=[], metavar=("NAME", "DEFAULT", "COMMENT"))
ap.add_argument("-o", "--out")
a = ap.parse_args()
if bool(a.entry) == bool(a.standalone):
    ap.error("give exactly one of --entry or --standalone")
root = Path(a.folder)
meta = {"id": a.id, "title": a.title, "description": a.description, "requires": a.requires,
        "intents": a.intents, "env": a.env, "props": []}
meta["standalone_entry" if a.standalone else "entry"] = a.standalone or a.entry
out = Path(a.out or f"{a.id}.bsmod")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("module.json", json.dumps(meta, indent=2))
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".txt", ".json", ".md") and "__pycache__" not in p.parts:
            z.write(p, p.relative_to(root).as_posix())
print("wrote", out)

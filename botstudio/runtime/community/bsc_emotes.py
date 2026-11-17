"""Emote Library — emotes stored as image files and posted as full-size attachments (Bot Forge Studio community module).

    /emote add <name> <image>     PNG or GIF (mods)          /emote remove <name>   (mods)
    /emote list                   (mods)                      /emote send [emote] [message]   (everyone)
    /emote browse [search]        visual picker, everyone     /emote panel           posts a permanent "Browse" button (mods)
    /emote bulk <zip>             add many at once (mods)     /emotes [search]       short alias for browse
    Right-click a message/user > Apps > "Browse emotes"       Right-click a message > Apps > "Import emotes from message" (mods)

Files live in data/emotes and are posted as attachments, so they are not subject to Discord's 256 KB emoji limit or the
200-emoji cap — only the server's attachment size limit.  Managing needs: server owner, Administrator, Manage Expressions,
or a role listed in the module's "Allowed role IDs".
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import sqlite3
import zipfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

ALLOWED_ROLE_IDS = set(%%allowed_role_ids%%)
MAX_BYTES = int(%%max_mb%% * 1024 * 1024)
ROOT = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = ROOT / "emotes_files.db"
EMOTE_DIR = ROOT / "emotes"
EMOTE_DIR.mkdir(parents=True, exist_ok=True)
NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGIC = (b"GIF87a", b"GIF89a")
log = logging.getLogger("bot.emotes")


# ------------------------------------------------------------------------- storage
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS emotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL,
        filename TEXT NOT NULL, added_by INTEGER NOT NULL)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_name ON emotes (guild_id, lower(name))")
    return conn


def db_add(guild_id: int, name: str, filename: str, added_by: int) -> None:
    with db_connect() as c:
        c.execute("INSERT INTO emotes (guild_id, name, filename, added_by) VALUES (?, ?, ?, ?)",
                  (guild_id, name, filename, added_by))


def db_get(guild_id: int, name: str):
    with db_connect() as c:
        return c.execute("SELECT filename, name FROM emotes WHERE guild_id = ? AND lower(name) = lower(?)",
                         (guild_id, name)).fetchone()


def db_list(guild_id: int):
    with db_connect() as c:
        return c.execute("SELECT filename, name FROM emotes WHERE guild_id = ? ORDER BY lower(name)", (guild_id,)).fetchall()


def db_remove(guild_id: int, name: str) -> None:
    with db_connect() as c:
        c.execute("DELETE FROM emotes WHERE guild_id = ? AND lower(name) = lower(?)", (guild_id, name))


# ------------------------------------------------------------------------- helpers
def is_authorized(interaction: discord.Interaction) -> bool:
    member, guild = interaction.user, interaction.guild
    if guild is None or not isinstance(member, discord.Member):
        return False
    if guild.owner_id == member.id:
        return True
    p = member.guild_permissions
    if p.administrator or p.manage_expressions:
        return True
    return any(r.id in ALLOWED_ROLE_IDS for r in member.roles)


def detect_type(data: bytes):
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(GIF_MAGIC):
        return "gif"
    return None


async def deny(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "You need to be the server owner, an admin, or a mod with **Manage Expressions** to use this.", ephemeral=True)


async def _names(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=n, value=n) for _, n in db_list(interaction.guild_id)
            if current.lower() in n.lower()][:25]


group = app_commands.Group(name="emote", description="Manage and use server emotes", guild_only=True)


@group.command(name="add", description="Add a new emote")
@app_commands.describe(name="Name for the emote (letters, numbers, underscores; 2-32 chars)", image="PNG or GIF file")
async def add(interaction: discord.Interaction, name: str, image: discord.Attachment) -> None:
    if not is_authorized(interaction):
        return await deny(interaction)
    if not NAME_RE.match(name):
        return await interaction.response.send_message(
            "Emote names must be 2-32 characters: letters, numbers and underscores only.", ephemeral=True)
    if image.size > MAX_BYTES:
        return await interaction.response.send_message(
            f"That file is {image.size / 1024:.0f} KB. The limit is {MAX_BYTES // 1024} KB.", ephemeral=True)
    if db_get(interaction.guild_id, name):
        return await interaction.response.send_message(f"This server already has an emote named `{name}`.", ephemeral=True)
    await interaction.response.defer(thinking=True)
    data = await image.read()
    kind = detect_type(data)
    if kind is None or len(data) > MAX_BYTES:
        return await interaction.followup.send("That doesn't look like a real PNG or GIF file (or it's too large).", ephemeral=True)
    filename = f"{interaction.guild_id}_{name.lower()}.{kind}"
    path = EMOTE_DIR / filename
    path.write_bytes(data)
    try:
        db_add(interaction.guild_id, name, filename, interaction.user.id)
    except sqlite3.IntegrityError:
        path.unlink(missing_ok=True)
        return await interaction.followup.send(f"This server already has an emote named `{name}`.", ephemeral=True)
    log.info("%s added emote %s in guild %s", interaction.user.id, name, interaction.guild_id)
    await interaction.followup.send(f"Added `{name}` ({len(data) / 1024:.0f} KB).",
                                    file=discord.File(path, filename=f"{name}.{kind}"))


@group.command(name="remove", description="Remove an emote")
@app_commands.describe(name="Name of the emote to remove")
async def remove(interaction: discord.Interaction, name: str) -> None:
    if not is_authorized(interaction):
        return await deny(interaction)
    row = db_get(interaction.guild_id, name)
    if row is None:
        return await interaction.response.send_message(f"No emote named `{name}` on this server.", ephemeral=True)
    filename, real = row
    (EMOTE_DIR / filename).unlink(missing_ok=True)
    db_remove(interaction.guild_id, real)
    await interaction.response.send_message(f"Removed `{real}`.")


remove.autocomplete("name")(_names)


@group.command(name="list", description="Show this server's emotes")
async def list_emotes(interaction: discord.Interaction) -> None:
    if not is_authorized(interaction):
        return await deny(interaction)
    rows = db_list(interaction.guild_id)
    if not rows:
        body = "This server has no emotes yet. Use `/emote add`."
    else:
        lines = []
        for filename, n in rows:
            p = EMOTE_DIR / filename
            lines.append(f"`{n}` ({p.stat().st_size / 1024:.0f} KB)" if p.exists() else f"`{n}` (missing file)")
        body = "\n".join(lines)[:1800]
    await interaction.response.send_message(body, ephemeral=True)


@group.command(name="send", description="Post an emote as a full-size image, optionally with text")
@app_commands.describe(emote="The emote to post", message="Optional text. Write :emotename: to also attach that emote")
async def send(interaction: discord.Interaction, emote: str | None = None, message: str | None = None) -> None:
    if not emote and not message:
        return await interaction.response.send_message("Pick an emote, write a message, or both.", ephemeral=True)
    await interaction.response.defer()
    rows = db_list(interaction.guild_id)
    if not rows:
        return await interaction.followup.send("This server has no emotes yet. A mod can add one with `/emote add`.", ephemeral=True)
    by_name = {n.lower(): (fn, n) for fn, n in rows}
    files: list[discord.File] = []
    seen: set[str] = set()

    def add_file(key: str) -> bool:
        entry = by_name.get(key.lower())
        if entry is None:
            return False
        filename, real = entry
        if filename in seen:
            return True
        path = EMOTE_DIR / filename
        if not path.exists():
            return False
        seen.add(filename)
        files.append(discord.File(path, filename=f"{real}{path.suffix}"))
        return True

    if emote and not add_file(emote):
        return await interaction.followup.send(f"No emote named `{emote}` on this server.", ephemeral=True)
    content = None
    if message:
        for m in re.finditer(r":([A-Za-z0-9_]{2,32}):", message):
            add_file(m.group(1))
        content = message.strip() or None
    if content and len(content) > 2000:
        return await interaction.followup.send("That message is too long.", ephemeral=True)
    if not files and not content:
        return await interaction.followup.send("No valid emotes found to send.", ephemeral=True)
    await interaction.followup.send(content=content, files=files[:10], allowed_mentions=discord.AllowedMentions.none())


send.autocomplete("emote")(_names)

# ------------------------------------------------------------------------- visual browser
PAGE_SIZE, COLS, CELL_W, CELL_H, THUMB = 12, 4, 176, 176, 128
SHEET_BG = (43, 45, 49)


def _font(size: int):
    for name in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _thumb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.seek(0)
        frame = im.convert("RGBA")
    frame.thumbnail((THUMB, THUMB))
    return frame


def render_sheet(entries: list) -> io.BytesIO:
    rows = -(-len(entries) // COLS)
    sheet = Image.new("RGB", (COLS * CELL_W, rows * CELL_H), SHEET_BG)
    draw, font = ImageDraw.Draw(sheet), _font(16)
    for i, (filename, name) in enumerate(entries):
        x, y = (i % COLS) * CELL_W, (i // COLS) * CELL_H
        try:
            t = _thumb(EMOTE_DIR / filename)
            sheet.paste(t, (x + (CELL_W - t.width) // 2, y + 8 + (THUMB - t.height) // 2), t)
        except Exception:
            draw.text((x + 40, y + 60), "missing file", fill=(200, 80, 80), font=font)
        label = name if len(name) <= 18 else name[:17] + "…"
        w = draw.textlength(label, font=font)
        draw.text((x + (CELL_W - w) / 2, y + THUMB + 18), label, fill=(220, 221, 222), font=font)
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    buf.seek(0)
    return buf


class EmoteBrowser(discord.ui.View):
    def __init__(self, rows: list) -> None:
        super().__init__(timeout=600)
        self.rows, self.page = rows, 0
        self.pages = max(1, -(-len(rows) // PAGE_SIZE))
        self._rebuild()

    def entries(self) -> list:
        return self.rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

    def _rebuild(self) -> None:
        self.clear_items()
        sel = discord.ui.Select(placeholder="Pick an emote to post...",
                                options=[discord.SelectOption(label=n, value=n) for _, n in self.entries()])
        sel.callback = self.on_pick
        self.add_item(sel)
        prev_b = discord.ui.Button(label="Prev", emoji="◀", disabled=self.page == 0)
        next_b = discord.ui.Button(label="Next", emoji="▶", disabled=self.page >= self.pages - 1)
        prev_b.callback, next_b.callback = self.on_prev, self.on_next
        self.add_item(prev_b)
        self.add_item(next_b)

    async def render(self):
        buf = await asyncio.to_thread(render_sheet, self.entries())
        embed = discord.Embed(title="Server emotes", color=discord.Color.blurple(), description=(
            f"Page {self.page + 1}/{self.pages} · {len(self.rows)} emotes\nPick one from the dropdown to post it in this channel."))
        embed.set_image(url="attachment://emotes.png")
        return embed, discord.File(buf, filename="emotes.png")

    async def _turn(self, interaction: discord.Interaction, delta: int) -> None:
        self.page = max(0, min(self.pages - 1, self.page + delta))
        self._rebuild()
        await interaction.response.defer()
        embed, file = await self.render()
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    async def on_prev(self, interaction): await self._turn(interaction, -1)
    async def on_next(self, interaction): await self._turn(interaction, +1)

    async def on_pick(self, interaction: discord.Interaction) -> None:
        row = db_get(interaction.guild_id, interaction.data["values"][0])
        path = EMOTE_DIR / row[0] if row else None
        if row is None or not path.exists():
            return await interaction.response.send_message("That emote no longer exists.", ephemeral=True)
        await interaction.response.send_message(content=interaction.user.mention,
                                                file=discord.File(path, filename=f"{row[1]}{path.suffix}"),
                                                allowed_mentions=discord.AllowedMentions.none())


async def open_browser(interaction: discord.Interaction, search: str | None = None) -> None:
    rows = db_list(interaction.guild_id)
    if search:
        rows = [r for r in rows if search.lower() in r[1].lower()]
    if not rows:
        msg = f"No emotes match `{search}`." if search else "This server has no emotes yet."
        return await interaction.response.send_message(msg, ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    view = EmoteBrowser(rows)
    embed, file = await view.render()
    await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)


class PanelView(discord.ui.View):
    """A permanent button so members can browse without typing a command."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Browse emotes", emoji="🖼️", style=discord.ButtonStyle.primary, custom_id="emotes:browse")
    async def browse_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_browser(interaction)


@group.command(name="browse", description="Browse all emotes with previews")
@app_commands.describe(search="Optional: only show emotes whose name contains this")
async def browse(interaction: discord.Interaction, search: str | None = None) -> None:
    await open_browser(interaction, search)


@group.command(name="panel", description="Post a permanent 'Browse emotes' button in this channel")
async def panel(interaction: discord.Interaction) -> None:
    if not is_authorized(interaction):
        return await deny(interaction)
    embed = discord.Embed(title="Server emotes", color=discord.Color.blurple(),
                          description="Click the button to browse every emote with a preview, then pick one to post it.")
    await interaction.response.send_message(embed=embed, view=PanelView())


@app_commands.command(name="emotes", description="Browse all emotes with previews")
@app_commands.describe(search="Optional: only show emotes whose name contains this")
@app_commands.guild_only()
async def emotes_cmd(interaction: discord.Interaction, search: str | None = None) -> None:
    await open_browser(interaction, search)


@app_commands.context_menu(name="Browse emotes")
@app_commands.guild_only()
async def browse_message_ctx(interaction: discord.Interaction, message: discord.Message) -> None:
    await open_browser(interaction)


@app_commands.context_menu(name="Browse emotes")
@app_commands.guild_only()
async def browse_user_ctx(interaction: discord.Interaction, user: discord.Member) -> None:
    await open_browser(interaction)


# ------------------------------------------------------------------------- bulk import
BULK_MAX_FILES, BULK_MAX_ZIP_BYTES, BULK_MAX_TOTAL_BYTES = 100, 25 * 1024 * 1024, 200 * 1024 * 1024


def clean_name(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")[:32]


def save_emote(guild_id: int, user_id: int, name: str, data: bytes):
    """Store one emote. Returns an error string, or None on success."""
    if not NAME_RE.match(name):
        return "bad name (need 2-32 letters/numbers/underscores)"
    if len(data) > MAX_BYTES:
        return "too large"
    kind = detect_type(data)
    if kind is None:
        return "not a real PNG/GIF"
    if db_get(guild_id, name):
        return "name already exists"
    filename = f"{guild_id}_{name.lower()}.{kind}"
    path = EMOTE_DIR / filename
    path.write_bytes(data)
    try:
        db_add(guild_id, name, filename, user_id)
    except sqlite3.IntegrityError:
        path.unlink(missing_ok=True)
        return "name already exists"
    return None


def import_zip(guild_id: int, user_id: int, blob: bytes):
    added, skipped, total = [], [], 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename).name
            if base.startswith(".") or "__MACOSX" in info.filename:
                continue
            stem, ext = os.path.splitext(base)
            if ext.lower() not in (".png", ".gif"):
                skipped.append(f"{base}: not a .png/.gif")
                continue
            if len(added) + len(skipped) >= BULK_MAX_FILES:
                skipped.append(f"(stopped at {BULK_MAX_FILES} files)")
                break
            with z.open(info) as f:
                data = f.read(MAX_BYTES + 1)          # capped read: header sizes can lie
            total += len(data)
            if total > BULK_MAX_TOTAL_BYTES:
                skipped.append("(stopped: archive too large when unpacked)")
                break
            name = clean_name(stem)
            err = save_emote(guild_id, user_id, name, data)
            (skipped.append(f"{base}: {err}") if err else added.append(name))
    return added, skipped


def bulk_summary(added: list, skipped: list) -> str:
    text = f"**Added {len(added)}** emote(s)."
    if added:
        text += "\n" + ", ".join(f"`{n}`" for n in added)
    if skipped:
        text += f"\n\n**Skipped {len(skipped)}:**\n" + "\n".join(f"- {s}" for s in skipped)
    return text[:1900]


@group.command(name="bulk", description="Add many emotes at once from a .zip of PNG/GIF files")
@app_commands.describe(archive="A .zip of PNG/GIF files. Each file name (minus extension) becomes the emote name")
async def bulk(interaction: discord.Interaction, archive: discord.Attachment) -> None:
    if not is_authorized(interaction):
        return await deny(interaction)
    if not archive.filename.lower().endswith(".zip"):
        return await interaction.response.send_message("Please attach a .zip file.", ephemeral=True)
    if archive.size > BULK_MAX_ZIP_BYTES:
        return await interaction.response.send_message(f"That zip is over {BULK_MAX_ZIP_BYTES // 1024 // 1024} MB.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    blob = await archive.read()
    try:
        added, skipped = await asyncio.to_thread(import_zip, interaction.guild_id, interaction.user.id, blob)
    except zipfile.BadZipFile:
        return await interaction.followup.send("That isn't a valid zip file.", ephemeral=True)
    await interaction.followup.send(bulk_summary(added, skipped), ephemeral=True)


@app_commands.context_menu(name="Import emotes from message")
@app_commands.guild_only()
async def import_message_ctx(interaction: discord.Interaction, message: discord.Message) -> None:
    """Mods: post up to 10 images in a message, right-click it, import them all."""
    if not is_authorized(interaction):
        return await deny(interaction)
    files = [a for a in message.attachments if a.filename.lower().endswith((".png", ".gif"))]
    if not files:
        return await interaction.response.send_message("That message has no PNG/GIF attachments.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    added, skipped = [], []
    for a in files:
        name = clean_name(os.path.splitext(a.filename)[0])
        if a.size > MAX_BYTES:
            skipped.append(f"{a.filename}: too large")
            continue
        err = await asyncio.to_thread(save_emote, interaction.guild_id, interaction.user.id, name, await a.read())
        (skipped.append(f"{a.filename}: {err}") if err else added.append(name))
    await interaction.followup.send(bulk_summary(added, skipped), ephemeral=True)


# ------------------------------------------------------------------------- cog entry point
_REGISTERED = (
    (group, None), (emotes_cmd, None),
    (browse_message_ctx, discord.AppCommandType.message), (browse_user_ctx, discord.AppCommandType.user),
    (import_message_ctx, discord.AppCommandType.message),
)


class EmotesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(PanelView())               # keeps the panel button working after restarts
        for cmd, _kind in _REGISTERED:
            self.bot.tree.add_command(cmd)

    async def cog_unload(self):
        for cmd, kind in _REGISTERED:
            self.bot.tree.remove_command(cmd.name, type=kind or discord.AppCommandType.chat_input)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmotesCog(bot))

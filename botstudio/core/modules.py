"""Module nodes: whole features shipped as pre-built cogs, configured through properties.

Templates use %%KEY%% tokens; each token is replaced with the Python *literal*
of the property value, so user text can never break out into code.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from .codegen import literal
from .model import NodeSpec, Prop, register


def fill(template: str, node, spec: NodeSpec) -> str:
    def sub(m):
        key = m.group(1)
        p = spec.prop(key)
        if p is None:
            return m.group(0)
        v = node.prop_value(key)
        if key.endswith("_list"):
            return repr([x.strip() for x in re.split(r"[,|\n]", str(v or "")) if x.strip()])
        if key.endswith("_ids"):
            toks = [x.strip("<@!#>") for x in re.split(r"[,\s]+", str(v or ""))]
            return repr([int(x) for x in toks if x.isdigit()])
        return literal(v, p.kind)
    return re.sub(r"%%([A-Za-z0-9_]+)%%", sub, template)


# =============================================================================
# Anonymous Post
# =============================================================================
ANON = '''from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
import bs_runtime as bs

NAME = %%command_name%%
MAX_LEN = %%max_length%%
LOG_AUTHOR = %%log_author%%
LOG_CHANNEL_ENV = %%log_channel_env%%


class AnonCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name=NAME, description="Post a message anonymously (hides who said it)")
    @app_commands.describe(message="The text to post anonymously")
    @app_commands.checks.cooldown(1, %%cooldown%%)
    @app_commands.guild_only()
    async def anon(self, interaction: discord.Interaction, message: str):
        message = message.strip()
        if not message:
            return await interaction.response.send_message("You need to actually write something.", ephemeral=True)
        if len(message) > MAX_LEN:
            return await interaction.response.send_message(
                f"Message is too long ({len(message)}/{MAX_LEN} characters).", ephemeral=True)
        try:
            await interaction.response.send_message("Posted anonymously.", ephemeral=True)
            sent = await interaction.channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return await interaction.followup.send("I can't post in this channel.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send("I couldn't post that message.", ephemeral=True)
        await self._staff_log(interaction, message, sent)

    async def _staff_log(self, interaction, content, sent):
        import os
        raw = os.getenv(LOG_CHANNEL_ENV, "").strip()
        if not raw.isdigit():
            return
        who = (f"{interaction.user.mention}\\n`{interaction.user}` (ID: `{interaction.user.id}`)"
               if LOG_AUTHOR else "Hidden by server settings")
        e = discord.Embed(title="Anonymous Post Log", colour=0xED4245, timestamp=discord.utils.utcnow())
        e.add_field(name="Posted by", value=who, inline=False)
        e.add_field(name="Channel", value=interaction.channel.mention, inline=True)
        e.add_field(name="Message", value=f"[Jump]({sent.jump_url})", inline=True)
        e.add_field(name="Content", value=content[:1000], inline=False)
        await bs.send(self.bot, int(raw), embed=e)

    @app_commands.command(name=NAME + "_about", description="How anonymous posting works here")
    async def about(self, interaction: discord.Interaction):
        note = ("Staff can see who posted for moderation purposes." if LOG_AUTHOR
                else "Authorship is not recorded.")
        await interaction.response.send_message(
            f"Use `/{NAME}` and I will post your message as me, so nobody sees who wrote it.\\n{note}",
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AnonCog(bot))
'''

register(NodeSpec(
    "module.anon_post", "Anonymous Post", "Module", "module",
    props=[
        Prop("command_name", "Command name", "str", "anon"),
        Prop("cooldown", "Cooldown (seconds)", "float", 5),
        Prop("max_length", "Max length", "int", 2000),
        Prop("log_author", "Staff log shows the author", "bool", True,
             help="Tells members honestly whether staff can unmask posts."),
        Prop("log_channel_env", "Staff log channel (env var name)", "str", "LOG_CHANNEL_ID"),
    ],
    files=lambda node, slug: {f"cogs/{slug}.py": fill(ANON, node, node.spec)},
    commands=lambda p: [str(p.get("command_name") or "anon"), str(p.get("command_name") or "anon") + "_about"],
    env=(("LOG_CHANNEL_ID", "", "Staff-only channel for anonymous post logs"),),
    description="Anonymous posting with an optional staff moderation log.",
))

# =============================================================================
# XP / Levels
# =============================================================================
XP = '''from __future__ import annotations

import random
import time

import discord
from discord import app_commands
from discord.ext import commands
import bs_runtime as bs

NS = %%namespace%%
XP_MIN, XP_MAX, COOLDOWN = %%xp_min%%, %%xp_max%%, %%cooldown%%
ANNOUNCE = %%announce%%


def level_for(xp: int) -> int:
    lvl = 0
    while xp >= 100 * (lvl + 1) ** 2 // 2:
        lvl += 1
    return lvl


class XpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last: dict[int, float] = {}

    @commands.Cog.listener("on_message")
    async def award(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        now = time.monotonic()
        if now - self._last.get(message.author.id, 0) < COOLDOWN:
            return
        self._last[message.author.id] = now
        key = f"{message.guild.id}:{message.author.id}"
        before = await bs.kv_get(NS, key, 0)
        after = await bs.kv_incr(NS, key, random.randint(XP_MIN, XP_MAX))
        if ANNOUNCE and level_for(after) > level_for(before):
            await bs.send(self.bot, message.channel,
                          f"{message.author.mention} reached **level {level_for(after)}**!")

    @app_commands.command(name="rank", description="Show your level")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        m = member or interaction.user
        xp = await bs.kv_get(NS, f"{interaction.guild_id}:{m.id}", 0)
        await bs.respond(interaction, embed=bs.embed(m.display_name, f"Level **{level_for(xp)}** · {xp} XP"))

    @app_commands.command(name="leaderboard", description="Top members by XP")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        rows = [(k, v) for k, v in await bs.kv_top(NS, 200) if k.startswith(f"{interaction.guild_id}:")][:10]
        lines = [f"**{i}.** <@{k.split(':')[1]}> — level {level_for(v)} ({v} XP)" for i, (k, v) in enumerate(rows, 1)]
        await bs.respond(interaction, embed=bs.embed("Leaderboard", "\\n".join(lines) or "Nobody has earned XP yet."))


async def setup(bot: commands.Bot):
    await bot.add_cog(XpCog(bot))
'''

register(NodeSpec(
    "module.xp", "XP & Levels", "Module", "module",
    props=[
        Prop("namespace", "Table name", "str", "xp"),
        Prop("xp_min", "XP per message (min)", "int", 5),
        Prop("xp_max", "XP per message (max)", "int", 15),
        Prop("cooldown", "Cooldown between awards (s)", "float", 60),
        Prop("announce", "Announce level-ups", "bool", True),
    ],
    files=lambda node, slug: {f"cogs/{slug}.py": fill(XP, node, node.spec)},
    commands=lambda p: ["rank", "leaderboard"],
    description="Message XP, levels, /rank and /leaderboard.",
))

# =============================================================================
# Hub Router (one node = one routing rule)
# =============================================================================
ROUTER = '''from __future__ import annotations

import discord
from discord.ext import commands
import bs_runtime as bs

HUB = %%hub_channel_id%%
DEST = %%destination_channel_id%%
SOURCES = set(%%source_ids%%)
USERS = set(%%user_ids%%)
KEYWORDS = [k.lower() for k in %%keywords_list%%]
REQUIRE_KEYWORD = %%require_keyword%%


def _texts(obj) -> str:
    parts = [getattr(obj, "content", "") or ""]
    for e in getattr(obj, "embeds", None) or []:
        parts += [str(x) for x in (e.title, e.description, e.url) if x]
        parts += [f"{f.name} {f.value}" for f in e.fields]
    return "\\n".join(parts)


def _blob(message: discord.Message) -> str:
    text = _texts(message)
    for snap in getattr(message, "message_snapshots", None) or []:
        text += "\\n" + _texts(snap)
    return text.lower()


def _origin_ids(message: discord.Message) -> set[int]:
    ref = message.reference
    if ref is not None and ref.channel_id:
        return {i for i in (ref.guild_id or (message.guild.id if message.guild else None), ref.channel_id) if i}
    return {message.channel.id, message.guild.id if message.guild else 0}


class RouterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def route(self, message: discord.Message):
        if message.guild is None or message.channel.id != HUB or message.author.id == self.bot.user.id:
            return
        if not (USERS or SOURCES or KEYWORDS):
            return  # a rule with no filters would swallow everything
        if USERS and message.author.id not in USERS:
            return
        if SOURCES and not (_origin_ids(message) & SOURCES):
            return
        if KEYWORDS:
            hit = any(k in _blob(message) for k in KEYWORDS)
            if REQUIRE_KEYWORD and not hit:
                return
            if not (USERS or SOURCES) and not hit:
                return
        await self.deliver(message)

    async def deliver(self, message: discord.Message):
        dest = self.bot.get_channel(DEST)
        if dest is None:
            try:
                dest = await self.bot.fetch_channel(DEST)
            except discord.HTTPException as e:
                return bs.log.warning("router: cannot access destination %s: %s", DEST, e)
        try:
            if isinstance(dest, discord.ForumChannel):
                title = (message.content or "Update").replace("\\n", " ")[:95] or "Update"
                await dest.create_thread(name=title, content=message.content or "\\u200b",
                                         embeds=list(message.embeds)[:10])
                return
            try:
                await message.forward(dest)
                return
            except (AttributeError, discord.HTTPException):
                pass
            files = [await a.to_file() for a in message.attachments]
            await dest.send(content=message.content or None, embeds=list(message.embeds), files=files)
        except discord.HTTPException as e:
            bs.log.warning("router: delivery failed: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(RouterCog(bot))
'''

register(NodeSpec(
    "module.hub_router", "Hub Router Rule", "Module", "module",
    props=[
        Prop("hub_channel_id", "Hub channel ID (followed channels land here)", "int", 0),
        Prop("destination_channel_id", "Destination channel / forum ID", "int", 0),
        Prop("source_ids", "Source server/channel IDs", "text", "", help="Comma or space separated."),
        Prop("user_ids", "Author IDs", "text", "", help="Up to 10, comma or space separated."),
        Prop("keywords_list", "Keywords (comma separated)", "text", ""),
        Prop("require_keyword", "Must also match a keyword", "bool", False),
    ],
    files=lambda node, slug: {f"cogs/{slug}.py": fill(ROUTER, node, node.spec)},
    intents=("message_content",),
    description="Routes posts from a hub channel (followed announcement channels) to a channel or forum.",
))

# =============================================================================
# Game UID registry
# =============================================================================
UID = '''from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
import bs_runtime as bs

GAMES = %%games_list%%
MAX_LEN = %%max_length%%
CHOICES = [app_commands.Choice(name=g, value=g) for g in GAMES[:25]]


def _ns(game: str) -> str:
    return "uid:" + game.lower()


class UidCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    uid = app_commands.Group(name="uid", description="Store and look up in-game IDs", guild_only=True)

    @uid.command(name="set", description="Save your in-game ID")
    @app_commands.choices(game=CHOICES)
    async def set_uid(self, interaction: discord.Interaction, game: app_commands.Choice[str], uid: str):
        uid = uid.strip()
        if not uid or len(uid) > MAX_LEN:
            return await bs.respond(interaction, f"UID must be 1-{MAX_LEN} characters.", ephemeral=True)
        await bs.kv_set(_ns(game.value), f"{interaction.guild_id}:{interaction.user.id}", uid)
        await bs.respond(interaction, f"Saved your **{game.value}** ID.", ephemeral=True)

    @uid.command(name="me", description="Show the IDs you saved")
    async def me(self, interaction: discord.Interaction):
        lines = []
        for g in GAMES:
            v = await bs.kv_get(_ns(g), f"{interaction.guild_id}:{interaction.user.id}", None)
            if v:
                lines.append(f"**{g}** → `{discord.utils.escape_markdown(str(v))}`")
        await bs.respond(interaction, "\\n".join(lines) or "You haven't saved any IDs yet.", ephemeral=True)

    @uid.command(name="lookup", description="Look up a member's in-game ID")
    @app_commands.choices(game=CHOICES)
    async def lookup(self, interaction: discord.Interaction, user: discord.Member, game: app_commands.Choice[str]):
        v = await bs.kv_get(_ns(game.value), f"{interaction.guild_id}:{user.id}", None)
        text = (f"**{user.display_name}** — {game.value}: `{discord.utils.escape_markdown(str(v))}`"
                if v else f"{user.display_name} has no {game.value} ID stored.")
        await bs.respond(interaction, text, ephemeral=True)

    @uid.command(name="remove", description="Delete your saved ID")
    @app_commands.choices(game=CHOICES)
    async def remove(self, interaction: discord.Interaction, game: app_commands.Choice[str]):
        await bs.kv_set(_ns(game.value), f"{interaction.guild_id}:{interaction.user.id}", None)
        await bs.respond(interaction, f"Removed your **{game.value}** ID.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UidCog(bot))
'''

register(NodeSpec(
    "module.uid_registry", "Game UID Registry", "Module", "module",
    props=[
        Prop("games_list", "Games (comma separated)", "text", "Game One, Game Two"),
        Prop("max_length", "Max UID length", "int", 64),
    ],
    files=lambda node, slug: {f"cogs/{slug}.py": fill(UID, node, node.spec)},
    commands=lambda p: ["uid"],
    description="Players save in-game IDs per game; /uid set, me, lookup, remove.",
))


# =============================================================================
# .bsmod bundles — drop-in modules (how larger existing bots ship as one node)
# =============================================================================

def dash_pages_from_meta(meta: dict):
    """module.json may declare dashboard pages: {"dashboard": {"pages": ["Title | leaderboard | table", ...]}}.
    ``{prop}`` inside a line is replaced by the node's property value. Returns None when there are none."""
    lines = (meta.get("dashboard") or {}).get("pages") if isinstance(meta.get("dashboard"), dict) else None
    if not lines:
        return None

    def pages(node, _lines=tuple(str(x) for x in lines)):
        from .nodes_web import parse_pages    # lazy: nodes_web needs the registry that imports this module
        text = "\n".join(re.sub(r"\{(\w+)\}", lambda m: str(node.prop_value(m.group(1)) or ""), ln) for ln in _lines)
        return parse_pages(text)
    return pages


def load_bsmod(path: str | Path) -> NodeSpec:
    """A .bsmod is a zip with module.json plus the bot's python files.

    module.json: {"id": "mybot", "title": "My Bot", "description": "...",
                  "entry": "cogs/mybot.py", "props": [{"key","label","kind","default","choices"}],
                  "intents": [...], "requires": [...], "env": [[NAME, default, comment]],
                  "commands": ["panel", ...]}
    A complete script-style bot (own client, own .run()) uses "standalone_entry": "main.py"
    instead of "entry": it is deployed as-is and run directly by the service.
    Every *.py inside is copied into the build; %%KEY%% tokens are filled from props.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("module.json"))
        files = {n: z.read(n).decode("utf-8") for n in z.namelist()
                 if n != "module.json" and not n.endswith("/") and n.endswith((".py", ".txt", ".json", ".md"))}
    entry = meta.get("standalone_entry") or meta["entry"]
    if entry not in files:
        raise ValueError(f"{path.name}: entry '{entry}' missing from bundle")
    props = [Prop(p["key"], p.get("label", ""), p.get("kind", "str"), p.get("default", ""),
                  tuple(p.get("choices", ())), p.get("help", "")) for p in meta.get("props", [])]
    spec = NodeSpec(
        f"bundle.{meta['id']}", meta["title"], "Module", "module", props=props,
        intents=tuple(meta.get("intents", ())), requires=tuple(meta.get("requires", ())),
        env=tuple(tuple(e) for e in meta.get("env", ())), description=meta.get("description", ""),
        bundle=files, standalone=meta.get("standalone_entry"),
    )

    def make_files(node, slug, _spec=spec, _entry=entry):
        out = {}
        for name, text in _spec.bundle.items():
            rendered = fill(text, node, _spec) if name.endswith(".py") else text
            out[name if _spec.standalone else (f"cogs/{slug}.py" if name == _entry else name)] = rendered
        return out

    spec.files = make_files
    spec.commands = lambda p, _c=tuple(meta.get("commands", ())): list(_c)
    spec.dash_pages = dash_pages_from_meta(meta)
    register(spec, replace=True)
    return spec

"""Per-user game UIDs, locked to a game profile.

Players set their in-game ID for a named game. Bracket lists and /uid lookup
surface those UIDs. UIDs never cross game profiles.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bsp_database import MAX_UID_LEN
from bsp_utils import game_autocomplete


async def _resolve_game(db, game_name: str):
    game = await db.get_game_profile_by_name(game_name)
    if game is None:
        profiles = await db.list_game_profiles()
        if not profiles:
            return None, (
                "No game profiles exist yet. Ask a mod/admin to create one with "
                "`/game add name:YourGame`."
            )
        names = ", ".join(f"**{p['name']}**" for p in profiles)
        return None, f"No game profile named **{game_name}**. Existing profiles: {names}"
    return game, None


def _esc(text: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


class UidCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(
        name="gameuid",
        description="Set or look up in-game user IDs (locked to a game profile)",
    )

    @group.command(
        name="set",
        description="Save your in-game UID for a game profile",
    )
    @app_commands.describe(
        game="Game profile this UID belongs to (e.g. Aion2)",
        uid="Your in-game user ID / character ID",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def set_uid(self, interaction: discord.Interaction, game: str, uid: str):
        uid = uid.strip()
        if not uid or len(uid) > MAX_UID_LEN:
            await interaction.response.send_message(
                f"UID must be 1–{MAX_UID_LEN} characters.",
                ephemeral=True,
            )
            return

        db = await self.bot.db_manager.get(interaction.guild_id)
        g, err = await _resolve_game(db, game)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        try:
            await db.set_user_uid(interaction.user.id, g["id"], uid)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.send_message(
            f"Saved your **{g['name']}** UID as `{_esc(uid)}`.\n"
            f"It will appear next to your name on `/bracket list` for that game.",
            ephemeral=True,
        )

    @group.command(
        name="remove",
        description="Remove your stored UID for a game profile",
    )
    @app_commands.describe(game="Game profile to clear the UID from")
    @app_commands.autocomplete(game=game_autocomplete)
    async def remove_uid(self, interaction: discord.Interaction, game: str):
        db = await self.bot.db_manager.get(interaction.guild_id)
        g, err = await _resolve_game(db, game)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        removed = await db.remove_user_uid(interaction.user.id, g["id"])
        if not removed:
            await interaction.response.send_message(
                f"You don't have a **{g['name']}** UID stored.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Removed your **{g['name']}** UID.",
            ephemeral=True,
        )

    @group.command(
        name="me",
        description="Show all game UIDs you have saved on this server",
    )
    async def me(self, interaction: discord.Interaction):
        db = await self.bot.db_manager.get(interaction.guild_id)
        rows = await db.list_user_uids(interaction.user.id)
        if not rows:
            await interaction.response.send_message(
                "You haven't saved any game UIDs yet. Use `/gameuid set game:… uid:…`.",
                ephemeral=True,
            )
            return

        lines = [f"**{r['game_name']}** → `{_esc(r['uid'])}`" for r in rows]
        embed = discord.Embed(
            title=f"Your game UIDs — {_esc(interaction.user.display_name)}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(
        name="lookup",
        description="Look up a member's stored game UIDs by Discord user",
    )
    @app_commands.describe(
        user="Discord member to look up",
        game="Optional: only show the UID for this game profile",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def lookup(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        game: str | None = None,
    ):
        db = await self.bot.db_manager.get(interaction.guild_id)

        if game:
            g, err = await _resolve_game(db, game)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            uid = await db.get_user_uid(user.id, g["id"])
            if uid is None:
                await interaction.response.send_message(
                    f"**{_esc(user.display_name)}** has no **{g['name']}** UID stored.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"**{_esc(user.display_name)}** — **{g['name']}** UID: `{_esc(uid)}`",
                ephemeral=True,
            )
            return

        rows = await db.list_user_uids(user.id)
        if not rows:
            await interaction.response.send_message(
                f"**{_esc(user.display_name)}** has no game UIDs stored on this server.",
                ephemeral=True,
            )
            return

        lines = [f"**{r['game_name']}** → `{_esc(r['uid'])}`" for r in rows]
        embed = discord.Embed(
            title=f"Game UIDs — {_esc(user.display_name)}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)



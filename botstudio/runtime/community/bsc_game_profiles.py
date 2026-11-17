"""Game profiles: isolate brackets and player UIDs per game on a multi-game server.

Admins/mods create a named profile (e.g. Aion2). Bracket roles and user UIDs are
then locked to that profile so one game's data never mixes with another's.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bsp_database import MAX_GAME_NAME_LEN
from bsp_utils import game_autocomplete


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(
        name="game",
        description="Manage game profiles that scope brackets and player UIDs",
    )

    @group.command(name="add", description="Create a new game profile (e.g. Aion2)")
    @app_commands.describe(name="Display name for the game (unique per server)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, name: str):
        name = name.strip()
        if not name or len(name) > MAX_GAME_NAME_LEN:
            await interaction.response.send_message(
                f"Game name must be 1–{MAX_GAME_NAME_LEN} characters.",
                ephemeral=True,
            )
            return

        db = await self.bot.db_manager.get(interaction.guild_id)
        existing = await db.get_game_profile_by_name(name)
        if existing:
            await interaction.response.send_message(
                f"A game profile named **{existing['name']}** already exists.",
                ephemeral=True,
            )
            return

        try:
            new_id = await db.add_game_profile(name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.send_message(
            f"Created game profile **{name}** (id `{new_id}`).\n"
            f"Next steps:\n"
            f"• `/bracket setrole game:{name} level:… role:…` to register bracket roles\n"
            f"• Or `/game migrate to:{name}` to move Legacy brackets onto this profile\n"
            f"• Players use `/gameuid set game:{name} uid:…` to store their in-game ID",
            ephemeral=True,
        )

    @group.command(name="edit", description="Rename an existing game profile")
    @app_commands.describe(
        name="Current name of the game profile",
        new_name="New display name",
    )
    @app_commands.autocomplete(name=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(self, interaction: discord.Interaction, name: str, new_name: str):
        db = await self.bot.db_manager.get(interaction.guild_id)
        game = await db.get_game_profile_by_name(name)
        if not game:
            await interaction.response.send_message(
                f"No game profile named **{name}**. Use `/game list` to see them.",
                ephemeral=True,
            )
            return

        new_name = new_name.strip()
        clash = await db.get_game_profile_by_name(new_name)
        if clash and clash["id"] != game["id"]:
            await interaction.response.send_message(
                f"**{clash['name']}** is already taken.",
                ephemeral=True,
            )
            return

        try:
            ok = await db.rename_game_profile(game["id"], new_name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        if not ok:
            await interaction.response.send_message("Rename failed.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Renamed **{game['name']}** → **{new_name}**.",
            ephemeral=True,
        )

    @group.command(
        name="remove",
        description="Delete a game profile (also removes its brackets and stored UIDs)",
    )
    @app_commands.describe(name="Name of the game profile to remove")
    @app_commands.autocomplete(name=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, name: str):
        db = await self.bot.db_manager.get(interaction.guild_id)
        game = await db.get_game_profile_by_name(name)
        if not game:
            await interaction.response.send_message(
                f"No game profile named **{name}**.",
                ephemeral=True,
            )
            return

        brackets = await db.list_bracket_roles(game["id"])
        ok = await db.remove_game_profile(game["id"])
        if not ok:
            await interaction.response.send_message("Remove failed.", ephemeral=True)
            return

        extra = ""
        if brackets:
            extra = f" Also removed {len(brackets)} bracket role registration(s)."
        await interaction.response.send_message(
            f"Deleted game profile **{game['name']}** and any UIDs tied to it.{extra}",
            ephemeral=True,
        )

    @group.command(
        name="migrate",
        description="Create Legacy (if needed), or move Legacy brackets/UIDs onto a named game profile",
    )
    @app_commands.describe(
        to="Game profile to move Legacy data onto. Leave blank to only create/seed Legacy.",
    )
    @app_commands.autocomplete(to=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def migrate(self, interaction: discord.Interaction, to: str | None = None):
        """Without `to`: ensure Legacy exists and absorb any orphaned pre-game brackets.
        With `to`: move brackets + UIDs from Legacy onto that named profile."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = await self.bot.db_manager.get(interaction.guild_id)

        # Always make sure Legacy exists / orphans are attached first.
        seed = await db.ensure_legacy_and_migrate()

        if to is None:
            parts = []
            if seed["created_legacy"]:
                parts.append("Created game profile **Legacy**.")
            else:
                parts.append("Game profile **Legacy** already exists.")
            if seed["brackets_migrated"]:
                parts.append(
                    f"Attached **{seed['brackets_migrated']}** orphaned bracket level(s) to Legacy."
                )
            else:
                parts.append("No orphaned brackets needed attaching.")
            parts.append(
                f"**Legacy** currently has **{seed['total_legacy_brackets']}** bracket level(s).\n"
                f"To move them onto a real game, run `/game migrate to:` and pick a profile "
                f"from the list (create one first with `/game add` if needed)."
            )
            await interaction.followup.send("\n".join(parts), ephemeral=True)
            return

        # Move Legacy → named profile
        target = await db.get_game_profile_by_name(to)
        if not target:
            await interaction.followup.send(
                f"No game profile named **{to}**. Create it with `/game add name:{to}` "
                f"or pick one from the autocomplete list.",
                ephemeral=True,
            )
            return

        if target["name"].lower() == "legacy":
            await interaction.followup.send(
                "Pick a different profile — migrating Legacy onto itself does nothing.",
                ephemeral=True,
            )
            return

        result = await db.migrate_legacy_to_game(target["id"])
        if result.get("error"):
            await interaction.followup.send(result["error"], ephemeral=True)
            return

        parts = [
            f"Migrated **Legacy** → **{result['target_name']}**:",
            f"• Brackets moved: **{result['brackets_moved']}**",
        ]
        if result["brackets_conflicted"]:
            parts.append(
                f"• Brackets left on Legacy (level already exists on "
                f"**{result['target_name']}**): **{result['brackets_conflicted']}**"
            )
        parts.append(f"• UIDs moved: **{result['uids_moved']}")
        if result["uids_skipped"]:
            parts.append(
                f"• UIDs skipped (player already had a UID on "
                f"**{result['target_name']}**): **{result['uids_skipped']}**"
            )
        parts.append(
            f"**Legacy** still has **{result['legacy_remaining_brackets']}** bracket level(s)."
        )
        if result["legacy_remaining_brackets"] == 0 and result["brackets_moved"]:
            parts.append(
                "Legacy is empty of brackets now — you can `/game remove name:Legacy` if you want."
            )
        await interaction.followup.send("\n".join(parts), ephemeral=True)

    @group.command(name="list", description="List all game profiles on this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_games(self, interaction: discord.Interaction):
        db = await self.bot.db_manager.get(interaction.guild_id)
        profiles = await db.list_game_profiles()
        if not profiles:
            await interaction.response.send_message(
                "No game profiles yet. Create one with `/game add name:YourGame`.",
                ephemeral=True,
            )
            return

        lines = []
        for p in profiles:
            brackets = await db.list_bracket_roles(p["id"])
            lines.append(
                f"**{p['name']}** (id `{p['id']}`) — {len(brackets)} bracket level(s) registered"
            )

        embed = discord.Embed(
            title="Game Profiles",
            description="\n".join(lines),
            color=discord.Color.teal(),
        )
        embed.set_footer(
            text="Brackets and player UIDs are locked to a profile so games never mix. "
            "Game names appear as a clickable list on commands that need them."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


from bsp_database import ensure_db, release_db  # noqa: E402


async def setup(bot: commands.Bot):
    ensure_db(bot)
    await bot.add_cog(GameCog(bot))
    import bsp_uid
    await bot.add_cog(bsp_uid.UidCog(bot))


async def teardown(bot: commands.Bot):
    await bot.remove_cog("UidCog")
    await release_db(bot)

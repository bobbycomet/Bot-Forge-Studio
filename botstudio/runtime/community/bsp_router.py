import json

import discord
from discord import app_commands
from discord.ext import commands

from bsp_utils import (
    deliver_to_mappings,
    describe_origin,
    explain_matching,
    find_matching_mappings,
    tier_label,
)


class RouterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatic operation: watch the hub channel and route matching posts."""
        if message.guild is None or message.author.id == self.bot.user.id:
            return

        db = await self.bot.db_manager.get(message.guild.id)
        hub_id = await db.get_hub_channel()
        if hub_id is None or message.channel.id != hub_id:
            return

        mappings = await db.list_mappings()
        matched = find_matching_mappings(mappings, message)
        if not matched:
            return

        results = await deliver_to_mappings(self.bot, message, matched)
        failures = [(m, info) for m, ok, info in results if not ok]
        for mapping, info in failures:
            try:
                await message.channel.send(
                    f"⚠️ Failed to route a message matched to mapping "
                    f"**#{mapping['id']} {mapping['name']}**: {info}",
                    delete_after=30,
                )
            except discord.HTTPException:
                pass

    @app_commands.command(
        name="router-test",
        description="Find the newest hub post from a given user ID and route it, to verify setup",
    )
    @app_commands.describe(
        user_id="The Discord user ID to search for in the hub channel",
        dry_run="Report which mappings would match without actually posting (default: False)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test(self, interaction: discord.Interaction, user_id: str, dry_run: bool = False):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not user_id.isdigit():
            await interaction.followup.send("`user_id` must be a numeric Discord ID.", ephemeral=True)
            return
        uid = int(user_id)

        db = await self.bot.db_manager.get(interaction.guild_id)
        hub_id = await db.get_hub_channel()
        if hub_id is None:
            await interaction.followup.send(
                "No hub channel set yet. Run `/router sethub` first.", ephemeral=True
            )
            return

        hub_channel = interaction.guild.get_channel(hub_id)
        if hub_channel is None:
            try:
                hub_channel = await self.bot.fetch_channel(hub_id)
            except discord.HTTPException as e:
                await interaction.followup.send(f"Couldn't access the hub channel: {e}", ephemeral=True)
                return

        mappings = await db.list_mappings()
        matches_some_mapping = any(
            (not json.loads(m["user_ids"])) or uid in json.loads(m["user_ids"]) for m in mappings
        )
        if not matches_some_mapping:
            await interaction.followup.send(
                f"No mapping currently contains user ID `{uid}`. Add one with `/router addmapping`.",
                ephemeral=True,
            )
            return

        found = None
        async for msg in hub_channel.history(limit=100):
            if msg.author.id == uid:
                found = msg
                break

        if found is None:
            await interaction.followup.send(
                f"Scanned the last 100 messages in {hub_channel.mention} and found none authored by ID `{uid}`.\n\n"
                f"**Note:** Discord's channel-follow feature crossposts messages via a webhook, which can "
                f"carry a different author ID than the original poster's account. Run `/router scan` to "
                f"see the author and origin IDs that actually appear in the hub.",
                ephemeral=True,
            )
            return

        # Full picture: every mapping and why it did or didn't match.
        report = explain_matching(mappings, found)
        matched = find_matching_mappings(mappings, found)
        matched_ids = {m["id"] for m in matched}

        lines = [f"Test message: {found.jump_url}", f"Origin: {describe_origin(found)}", ""]
        for mapping, tier in report:
            if mapping["id"] in matched_ids:
                marker = "✅ routed"
            elif tier is not None:
                marker = "➖ matched but outranked"
            else:
                marker = "❌ no match"
            lines.append(f"**#{mapping['id']} {mapping['name']}** — {marker} ({tier_label(tier)})")

        if dry_run:
            await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)
            return

        results = await deliver_to_mappings(self.bot, found, matched)
        lines.append("")
        for mapping, ok, info in results:
            lines.append(f"{'✅' if ok else '❌'} #{mapping['id']}: {info}")

        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)



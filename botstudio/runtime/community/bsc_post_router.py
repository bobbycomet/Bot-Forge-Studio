"""Post Router — watch a hub channel and route matching posts to channels, forums or threads (Bot Forge Studio community module).

Admins create mappings (/router addmapping): author IDs, source servers/channels, keywords, and a destination.
Posts that land in the hub (channel follows, forwards, webhooks) are matched by specificity — source beats keyword
beats author — and copied or forwarded to every mapping at the winning tier.  /router scan shows the IDs that
actually appear in the hub; /router-test dry-runs a mapping against the newest matching post.
"""
import json

import discord
from discord import app_commands
from discord.ext import commands

from bsp_utils import (
    message_origin,
    message_snapshots,
    parse_keywords,
    parse_mapping_ids,
    parse_source_ids,
    parse_user_ids,
    resolve_user_labels,
    row_get,
)


def resolve_destination_mention(guild: discord.Guild, channel_id: int) -> str:
    """Best-effort mention for a stored destination, which may be a channel, a
    forum, or a specific thread inside a forum."""
    chan = guild.get_channel_or_thread(channel_id)
    return chan.mention if chan else f"`{channel_id}` (not found)"


class MappingModal(discord.ui.Modal):
    """Text-field portion of a mapping (name, user IDs, source IDs, keywords,
    optional thread-ID override).

    Non-thread destinations (a plain text channel, or "post new threads in this
    forum") are picked via the parent slash command's native channel-select
    option, since Discord modals only support text inputs. But Discord's
    channel picker can never list *threads* inside a forum — that's an API
    limitation — so routing to one *specific existing* forum thread is done
    here instead: paste the thread's ID and it overrides whatever
    channel/forum was selected.

    The **origin** field is what lets one user ID feed several mappings: it
    pins a mapping to the source server and/or source channel a post came
    from, so the same author posting in two different servers lands in two
    different destinations.

    `db` here is already a Database scoped to the command's own guild (see
    DatabaseManager in database.py) — nothing in this modal needs to know or
    check the guild ID itself.
    """

    def __init__(self, destination, db, existing=None):
        super().__init__(title="Edit Mapping" if existing else "New Source → Destination Mapping")
        self.destination = destination  # discord.TextChannel | discord.ForumChannel | None
        self.db = db
        self.existing = existing

        default_ids = ", ".join(str(i) for i in json.loads(existing["user_ids"])) if existing else None
        default_sources = (
            ", ".join(str(i) for i in json.loads(row_get(existing, "source_ids", "[]"))) if existing else None
        )
        default_kw = ", ".join(json.loads(existing["keywords"])) if existing else None
        # Only pre-fill the thread-ID field if the mapping's current destination
        # is actually a thread (so we don't overwrite a forum-level pick).
        default_thread_id = None
        if existing and destination is None:
            default_thread_id = str(existing["destination_channel_id"])

        self.name_input = discord.ui.TextInput(
            label="Mapping name",
            placeholder="e.g. Game Studio X news",
            default=existing["name"] if existing else None,
            max_length=100,
        )
        self.user_ids_input = discord.ui.TextInput(
            label="Source user ID(s) — up to 10, comma sep.",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder="123456789012345678, 234567890123456789",
            default=default_ids,
        )
        self.source_ids_input = discord.ui.TextInput(
            label="Origin server and/or channel ID(s)",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder="Server or channel IDs the post must come from — run /router scan to find them",
            default=default_sources,
        )
        self.keywords_input = discord.ui.TextInput(
            label="Keywords (optional, comma separated)",
            style=discord.TextStyle.paragraph,
            required=False,
            placeholder="starcraft, sc2, zerg",
            default=default_kw,
        )
        self.thread_id_input = discord.ui.TextInput(
            label="OR: thread ID (overrides destination)",
            required=False,
            placeholder="Paste a forum thread's ID here to target that exact thread",
            default=default_thread_id,
        )

        for item in (
            self.name_input,
            self.user_ids_input,
            self.source_ids_input,
            self.keywords_input,
            self.thread_id_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        user_ids = parse_user_ids(self.user_ids_input.value)
        source_ids = parse_source_ids(self.source_ids_input.value)
        if not user_ids and not source_ids:
            await interaction.response.send_message(
                "A mapping needs at least one filter: some user ID(s), some origin server/channel ID(s), "
                "or both. (A mapping with neither would never match anything.)",
                ephemeral=True,
            )
            return

        # --- resolve the destination: thread-ID override beats the channel picker ---
        final_destination_id = None
        final_destination_label = None

        thread_raw = self.thread_id_input.value.strip()
        if thread_raw:
            if not thread_raw.isdigit():
                await interaction.response.send_message(
                    "The thread ID must be numeric. Right-click the thread (with Developer Mode enabled in "
                    "Discord settings) and choose **Copy Thread ID**.",
                    ephemeral=True,
                )
                return
            thread_id = int(thread_raw)
            thread_obj = interaction.guild.get_thread(thread_id)
            if thread_obj is None:
                try:
                    thread_obj = await interaction.client.fetch_channel(thread_id)
                except discord.HTTPException:
                    thread_obj = None
            if not isinstance(thread_obj, discord.Thread) or thread_obj.guild.id != interaction.guild_id:
                await interaction.response.send_message(
                    f"`{thread_id}` doesn't look like a valid thread ID in this server — double-check it and "
                    f"make sure the bot can see it (it needs access to the parent forum/channel).",
                    ephemeral=True,
                )
                return
            final_destination_id = thread_obj.id
            final_destination_label = thread_obj.mention
        elif self.destination is not None:
            final_destination_id = self.destination.id
            final_destination_label = self.destination.mention
        elif self.existing is not None:
            # Editing without changing the destination — keep what's already there.
            final_destination_id = self.existing["destination_channel_id"]
            final_destination_label = resolve_destination_mention(interaction.guild, final_destination_id)
        else:
            await interaction.response.send_message(
                "You need to either pick a destination channel/forum on the command, or paste a thread ID "
                "in this modal.",
                ephemeral=True,
            )
            return

        keywords = parse_keywords(self.keywords_input.value)
        keyword_mode = bool(keywords)  # keywords present = keyword matching required

        if self.existing:
            await self.db.update_mapping(
                self.existing["id"],
                name=self.name_input.value,
                destination_channel_id=final_destination_id,
                user_ids=user_ids,
                source_ids=source_ids,
                keyword_mode=keyword_mode,
                keywords=keywords,
            )
            msg = f"Updated mapping **#{self.existing['id']}** → {final_destination_label}"
        else:
            new_id = await self.db.add_mapping(
                self.name_input.value,
                final_destination_id,
                user_ids,
                keyword_mode,
                keywords,
                source_ids=source_ids,
            )
            msg = f"Created mapping **#{new_id}** ({self.name_input.value}) → {final_destination_label}"

        detail = []
        detail.append(f"user IDs: {', '.join(str(i) for i in user_ids) or '— (any author)'}")
        detail.append(f"origin IDs: {', '.join(str(i) for i in source_ids) or '— (any origin)'}")
        detail.append(f"keywords: {', '.join(keywords) or '— (not required)'}")
        await interaction.response.send_message(msg + "\n" + "\n".join(detail), ephemeral=True)


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="router", description="Configure the post router")

    @group.command(name="sethub", description="Set the hub channel that receives followed news posts")
    @app_commands.describe(channel="The text channel where followed announcement posts land")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sethub(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db = await self.bot.db_manager.get(interaction.guild_id)
        await db.set_hub_channel(channel.id)
        await interaction.response.send_message(
            f"Hub channel set to {channel.mention}. Make sure followed news channels are set to post into it "
            f"(right-click the source channel in the other server → Follow → choose this channel).",
            ephemeral=True,
        )

    @group.command(name="addmapping", description="Map source user ID(s) and/or an origin server to a destination")
    @app_commands.describe(
        destination="Forum (new post per message) or text channel. Leave blank if you'll paste a "
        "thread ID in the modal instead."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def addmapping(
        self,
        interaction: discord.Interaction,
        destination: discord.TextChannel | discord.ForumChannel | None = None,
    ):
        db = await self.bot.db_manager.get(interaction.guild_id)
        await interaction.response.send_modal(MappingModal(destination, db))

    @group.command(name="editmapping", description="Edit an existing mapping")
    @app_commands.describe(
        mapping_id="ID shown in /router listmappings",
        destination="New destination channel/forum. Leave blank to keep the current destination, or to "
        "paste a thread ID in the modal instead.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def editmapping(
        self,
        interaction: discord.Interaction,
        mapping_id: int,
        destination: discord.TextChannel | discord.ForumChannel | None = None,
    ):
        db = await self.bot.db_manager.get(interaction.guild_id)
        existing = await db.get_mapping(mapping_id)
        if not existing:
            await interaction.response.send_message("No mapping with that ID in this server.", ephemeral=True)
            return
        await interaction.response.send_modal(MappingModal(destination, db, existing=existing))

    @group.command(name="removemapping", description="Remove a mapping")
    @app_commands.describe(mapping_id="ID shown in /router listmappings")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removemapping(self, interaction: discord.Interaction, mapping_id: int):
        db = await self.bot.db_manager.get(interaction.guild_id)
        existing = await db.get_mapping(mapping_id)
        if not existing:
            await interaction.response.send_message("No mapping with that ID in this server.", ephemeral=True)
            return
        await db.remove_mapping(mapping_id)
        await interaction.response.send_message(f"Removed mapping #{mapping_id}.", ephemeral=True)

    @group.command(name="listmappings", description="List all configured mappings for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def listmappings(self, interaction: discord.Interaction):
        db = await self.bot.db_manager.get(interaction.guild_id)
        rows = await db.list_mappings()
        if not rows:
            await interaction.response.send_message("No mappings configured yet.", ephemeral=True)
            return

        all_user_ids = [i for r in rows for i in json.loads(r["user_ids"])]
        name_by_id = await resolve_user_labels(self.bot, all_user_ids)

        lines = []
        for r in rows:
            dest_str = resolve_destination_mention(interaction.guild, r["destination_channel_id"])
            user_ids = json.loads(r["user_ids"])
            ids = ", ".join(name_by_id[i] for i in user_ids) or "any"
            sources = ", ".join(str(i) for i in json.loads(row_get(r, "source_ids", "[]"))) or "any"
            kw = ", ".join(json.loads(r["keywords"])) or "—"
            lines.append(
                f"**#{r['id']} {r['name']}** → {dest_str}\n"
                f"  user IDs: {ids}\n"
                f"  origin IDs: {sources}\n"
                f"  keywords: {kw}"
            )

        embed = discord.Embed(
            title="Post Router Mappings",
            description="\n\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Origin IDs beat keywords, keywords beat author-only. Ties fan out to all.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="settings", description="Edit a mapping's filters (users, origins, keywords)")
    @app_commands.describe(mapping_id="ID shown in /router listmappings")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings_cmd(self, interaction: discord.Interaction, mapping_id: int):
        db = await self.bot.db_manager.get(interaction.guild_id)
        existing = await db.get_mapping(mapping_id)
        if not existing:
            await interaction.response.send_message("No mapping with that ID in this server.", ephemeral=True)
            return
        dest = interaction.guild.get_channel_or_thread(existing["destination_channel_id"])
        # dest may legitimately be None (e.g. it's a thread the bot hasn't cached) —
        # that's fine, the modal will fall back to keeping the existing destination ID.
        await interaction.response.send_modal(MappingModal(dest, db, existing=existing))

    @group.command(
        name="adduser",
        description="Add a user ID to some or all mappings in this server at once",
    )
    @app_commands.describe(
        user_id="The Discord user ID to add",
        mapping_ids='Comma-separated mapping IDs (from /router listmappings), or "all"',
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def adduser(self, interaction: discord.Interaction, user_id: str, mapping_ids: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not user_id.isdigit():
            await interaction.followup.send("`user_id` must be a numeric Discord ID.", ephemeral=True)
            return
        uid = int(user_id)

        target_ids = parse_mapping_ids(mapping_ids)
        if target_ids is not None and not target_ids:
            await interaction.followup.send(
                '`mapping_ids` didn\'t parse to anything — use a comma-separated list of IDs, or "all".',
                ephemeral=True,
            )
            return

        db = await self.bot.db_manager.get(interaction.guild_id)
        changed = await db.bulk_add_user_id(uid, mapping_ids=target_ids)
        label = (await resolve_user_labels(self.bot, [uid]))[uid]

        if not changed:
            await interaction.followup.send(
                f"{label} was already present in every targeted mapping — nothing to change.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Added {label} to mapping(s): {', '.join(f'#{i}' for i in changed)}.",
            ephemeral=True,
        )

    @group.command(
        name="removeuser",
        description="Remove a user ID from some or all mappings in this server at once",
    )
    @app_commands.describe(
        user_id="The Discord user ID to remove",
        mapping_ids='Comma-separated mapping IDs to limit this to, or "all" (default: all)',
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removeuser(
        self, interaction: discord.Interaction, user_id: str, mapping_ids: str = "all"
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not user_id.isdigit():
            await interaction.followup.send("`user_id` must be a numeric Discord ID.", ephemeral=True)
            return
        uid = int(user_id)

        target_ids = parse_mapping_ids(mapping_ids)
        if target_ids is not None and not target_ids:
            await interaction.followup.send(
                '`mapping_ids` didn\'t parse to anything — use a comma-separated list of IDs, or "all".',
                ephemeral=True,
            )
            return

        db = await self.bot.db_manager.get(interaction.guild_id)
        changed = await db.bulk_remove_user_id(uid, mapping_ids=target_ids)
        label = (await resolve_user_labels(self.bot, [uid]))[uid]

        if not changed:
            await interaction.followup.send(
                f"{label} wasn't listed in any targeted mapping — nothing to change.",
                ephemeral=True,
            )
            return

        # Flag any mapping that now has no filters left at all — it's still
        # there, but it will never match anything until it gets a new user ID,
        # origin ID, or is removed.
        emptied = []
        for mid in changed:
            row = await db.get_mapping(mid)
            if not json.loads(row["user_ids"]) and not json.loads(row_get(row, "source_ids", "[]")):
                emptied.append(mid)

        msg = f"Removed {label} from mapping(s): {', '.join(f'#{i}' for i in changed)}."
        if emptied:
            msg += (
                f"\n⚠️ Mapping(s) {', '.join(f'#{i}' for i in emptied)} now have no user IDs *and* no "
                f"origin IDs left — they won't match anything until you add a filter or remove them."
            )
        await interaction.followup.send(msg, ephemeral=True)

    @group.command(
        name="scan",
        description="Show the author + origin server/channel IDs of recent hub posts, for building mappings",
    )
    @app_commands.describe(limit="How many recent hub messages to inspect (default 50, max 200)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan(self, interaction: discord.Interaction, limit: int = 50):
        await interaction.response.defer(ephemeral=True, thinking=True)
        limit = max(1, min(limit, 200))

        db = await self.bot.db_manager.get(interaction.guild_id)
        hub_id = await db.get_hub_channel()
        if hub_id is None:
            await interaction.followup.send("No hub channel set yet. Run `/router sethub` first.", ephemeral=True)
            return

        hub_channel = interaction.guild.get_channel(hub_id)
        if hub_channel is None:
            try:
                hub_channel = await self.bot.fetch_channel(hub_id)
            except discord.HTTPException as e:
                await interaction.followup.send(f"Couldn't access the hub channel: {e}", ephemeral=True)
                return

        seen: dict[tuple, dict] = {}
        async for msg in hub_channel.history(limit=limit):
            guild_id, channel_id = message_origin(msg)
            key = (msg.author.id, guild_id, channel_id)
            entry = seen.setdefault(
                key,
                {
                    "author": f"{msg.author} ({'webhook' if msg.webhook_id else 'user/bot'})",
                    "count": 0,
                    "forwarded": bool(message_snapshots(msg)),
                },
            )
            entry["count"] += 1

        if not seen:
            await interaction.followup.send(f"No messages found in {hub_channel.mention}.", ephemeral=True)
            return

        lines = []
        for (author_id, guild_id, channel_id), info in sorted(
            seen.items(), key=lambda kv: kv[1]["count"], reverse=True
        )[:15]:
            lines.append(
                f"**{info['author']}** ×{info['count']}{' (forwarded)' if info['forwarded'] else ''}\n"
                f"  user ID: `{author_id}`\n"
                f"  origin server: `{guild_id}` | origin channel: `{channel_id}`"
            )

        embed = discord.Embed(
            title=f"Hub scan — last {limit} messages",
            description="\n\n".join(lines)[:4000],
            color=discord.Color.green(),
        )
        embed.set_footer(
            text="Paste an origin server or channel ID into a mapping's origin field to pin it to that source."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


from bsp_database import ensure_db, release_db  # noqa: E402


async def setup(bot: commands.Bot):
    ensure_db(bot)
    await bot.add_cog(SetupCog(bot))
    import bsp_router
    await bot.add_cog(bsp_router.RouterCog(bot))


async def teardown(bot: commands.Bot):
    await bot.remove_cog("RouterCog")       # defined in a support module, so the extension does not own it
    await release_db(bot)

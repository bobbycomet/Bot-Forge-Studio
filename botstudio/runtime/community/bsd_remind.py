"""/reminder commands: one-off and recurring reminders that post a message in a channel."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bsd_common import CROWN, GOLD
from bsd_timeutil import format_interval, get_zone, prefill_when, when_text
from bsd_ui import (
    ReminderModal,
    ReminderSetupView,
    default_channel,
    notify_summary,
    reminder_text,
    trim,
)


@app_commands.guild_only()
class Remind(
    commands.GroupCog,
    group_name="reminder",
    group_description="One-off and recurring reminders that post a message in a channel",
):
    def __init__(self, bot) -> None:
        self.bot = bot
        super().__init__()

    @staticmethod
    async def _say(interaction: discord.Interaction, msg: str) -> None:
        await interaction.response.send_message(msg, ephemeral=True)

    async def _get(self, interaction: discord.Interaction, raw: str) -> Optional[dict]:
        r = await self.bot.db.get_reminder(int(raw)) if raw and raw.isdigit() else None
        if r is None or r["guild_id"] != interaction.guild_id:
            await self._say(
                interaction,
                "❌ I couldn't find that reminder — pick one from the suggestions.",
            )
            return None
        if r["owner_id"] != interaction.user.id and not interaction.permissions.manage_guild:
            await self._say(interaction, "❌ That reminder belongs to someone else.")
            return None
        if r["status"] != "pending":
            await self._say(interaction, f"❌ That reminder is already {r['status']}.")
            return None
        return r

    async def reminder_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        mgr = interaction.permissions.manage_guild
        rows = await self.bot.db.reminders_for(
            interaction.guild_id, None if mgr else interaction.user.id, 100
        )
        tz = get_zone(await self.bot.db.get_timezone(interaction.user.id) or "UTC")
        cur, out = current.lower(), []
        for r in rows:
            when = datetime.fromtimestamp(r["due_at"], tz).strftime("%b %d %H:%M")
            rep = format_interval(r.get("interval_seconds"))
            label = f"#{r['id']} {trim(reminder_text(r), 45)} · {when}"
            if rep != "one-shot":
                label += f" · {rep}"
            if cur in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=str(r["id"])))
        return out[:25]

    @app_commands.command(
        name="create", description="Set a reminder that posts a message in a channel."
    )
    async def create(self, interaction: discord.Interaction) -> None:
        tz = await self.bot.db.get_timezone(interaction.user.id)
        view = ReminderSetupView(
            self.bot,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=await default_channel(self.bot, interaction),
            tz=tz or "UTC",
            tz_set=tz is not None,
            notify_ids=[interaction.user.id],
            title="New reminder",
            allow_everyone=interaction.permissions.mention_everyone,
        )
        await interaction.response.send_modal(ReminderModal(view))

    @app_commands.command(name="list", description="See your upcoming reminders.")
    async def list_(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.reminders_for(
            interaction.guild_id, interaction.user.id, 15
        )
        if not rows:
            return await self._say(
                interaction,
                "You have no upcoming reminders — set one with **/reminder create**.",
            )
        lines = []
        for r in rows:
            who = notify_summary(r)
            ch = f"<#{r['channel_id']}>" if r["channel_id"] else "DM"
            rep = format_interval(r.get("interval_seconds"))
            extra = f" · 🔁 {rep}" if rep != "one-shot" else ""
            lines.append(
                f"`#{r['id']}` {trim(reminder_text(r), 80)}\n"
                f"↳ {when_text(r['due_at'])} · {ch} · {who}{extra}"
            )
        e = discord.Embed(
            title=f"{CROWN} Upcoming reminders",
            colour=GOLD,
            description="\n".join(lines)[:4000],
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(
        name="edit", description="Change a reminder's message, time, channel, or pings."
    )
    @app_commands.describe(reminder="The reminder to edit")
    @app_commands.autocomplete(reminder=reminder_autocomplete)
    async def edit(self, interaction: discord.Interaction, reminder: str) -> None:
        r = await self._get(interaction, reminder)
        if not r:
            return
        tz = await self.bot.db.get_timezone(interaction.user.id)
        view = ReminderSetupView(
            self.bot,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=r["channel_id"] or await default_channel(self.bot, interaction),
            tz=tz or "UTC",
            tz_set=tz is not None,
            notify_ids=json.loads(r["notify_ids"] or "[]"),
            notify_roles=json.loads(r.get("notify_roles") or "[]"),
            ping_everyone=bool(r.get("ping_everyone")),
            interval_seconds=r.get("interval_seconds"),
            title="Edit reminder",
            message_required=r["list_id"] is None,
            link_list_id=r["list_id"],
            link_task_id=r["task_id"],
            editing=r,
            message=r["message"],
            when_raw=prefill_when(r["kind"], r["due_at"], True, tz or "UTC"),
            context=reminder_text(r) if not r["message"] else "",
            allow_everyone=interaction.permissions.mention_everyone,
        )
        await interaction.response.send_modal(ReminderModal(view))

    @app_commands.command(name="cancel", description="Cancel an upcoming reminder.")
    @app_commands.describe(reminder="The reminder to cancel")
    @app_commands.autocomplete(reminder=reminder_autocomplete)
    async def cancel(self, interaction: discord.Interaction, reminder: str) -> None:
        r = await self._get(interaction, reminder)
        if not r:
            return
        await self.bot.db.update_reminder(r["id"], status="cancelled")
        await self._say(interaction, f"🗑️ Cancelled reminder `#{r['id']}`.")

    @app_commands.command(
        name="digest", description="Show reminders due in the next 48 hours."
    )
    async def digest(self, interaction: discord.Interaction) -> None:
        import time

        now = int(time.time())
        rows = await self.bot.db.due_soon(interaction.guild_id, 48 * 3600, now)
        # Filter to this user's unless manage_guild
        if not interaction.permissions.manage_guild:
            rows = [r for r in rows if r["owner_id"] == interaction.user.id]
        if not rows:
            return await self._say(
                interaction, "Nothing due in the next 48 hours."
            )
        lines = []
        for r in rows[:25]:
            who = notify_summary(r)
            rep = format_interval(r.get("interval_seconds"))
            extra = f" · 🔁 {rep}" if rep != "one-shot" else ""
            lines.append(
                f"• {when_text(r['due_at'])} — {trim(reminder_text(r), 70)}{extra}\n"
                f"  {who}"
            )
        e = discord.Embed(
            title="📋 Due in the next 48 hours",
            colour=GOLD,
            description="\n".join(lines)[:4000],
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

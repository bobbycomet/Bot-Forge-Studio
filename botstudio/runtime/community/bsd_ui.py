"""Shared UI: ping/channel pickers and the reminder setup flow (modal + preview screen)."""
from __future__ import annotations

import json
import time
from typing import Optional

import discord

from bsd_common import GOLD, MAX_PINGED, MAX_ROLE_PINGS, report_error
from bsd_timeutil import (
    ParsedWhen, TimeParseError, format_interval, parse_interval, parse_when, when_text,
)

# ───────────────────────────── helpers ─────────────────────────────
def pings(ids: list[int], fallback: str = "Nobody") -> str:
    """Compact ping summary so embeds stay under Discord's size limits."""
    if not ids:
        return fallback
    if len(ids) <= 3:
        return " ".join(f"<@{i}>" for i in ids)
    return f"{len(ids)} people"


def role_pings(ids: list[int], fallback: str = "") -> str:
    if not ids:
        return fallback
    if len(ids) <= 3:
        return " ".join(f"<@&{i}>" for i in ids)
    return f"{len(ids)} roles"


def trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def user_defaults(ids: list[int]) -> list[discord.SelectDefaultValue]:
    return [
        discord.SelectDefaultValue(id=i, type=discord.SelectDefaultValueType.user)
        for i in ids[:MAX_PINGED]
    ]


def role_defaults(ids: list[int]) -> list[discord.SelectDefaultValue]:
    return [
        discord.SelectDefaultValue(id=i, type=discord.SelectDefaultValueType.role)
        for i in ids[:MAX_ROLE_PINGS]
    ]


def channel_defaults(cid: Optional[int]) -> list[discord.SelectDefaultValue]:
    if not cid:
        return []
    return [discord.SelectDefaultValue(id=cid, type=discord.SelectDefaultValueType.channel)]


def describe_when(parsed: ParsedWhen, raw: str, starts: str) -> str:
    if parsed.kind == "timer":
        return f"⏱️ **{raw}** countdown — starts when you press {starts}"
    return f"📅 {when_text(parsed.due_at)}"


def reminder_text(r: dict) -> str:
    """Best short description of a reminder row (joined with list/task names)."""
    if r.get("message"):
        return r["message"]
    if r.get("task_id"):
        return f"Task #{r['task_position']} · {r['task_name']} ({r['list_name']})"
    if r.get("list_id"):
        return f"Countdown · {r['list_name']}"
    return "(no message)"


def notify_summary(r: dict) -> str:
    users = json.loads(r.get("notify_ids") or "[]")
    roles = json.loads(r.get("notify_roles") or "[]")
    parts = []
    if r.get("ping_everyone"):
        parts.append("@everyone")
    if roles:
        parts.append(role_pings(roles))
    if users:
        parts.append(pings(users))
    return " · ".join(parts) if parts else "Nobody — message only"


async def default_channel(bot, interaction: discord.Interaction) -> Optional[int]:
    """Server's configured reminder channel, else the channel the command was used in."""
    return await bot.db.get_channel(interaction.guild_id) or interaction.channel_id


# ───────────────────────────── reminder modal ─────────────────────────────
class ReminderModal(discord.ui.Modal):
    """Message + when + optional repeat. Fills in a ReminderSetupView and shows/refreshes it."""

    def __init__(self, setup: "ReminderSetupView") -> None:
        super().__init__(title=setup.title[:45])
        self.setup = setup
        self.msg_in = discord.ui.TextInput(
            label="Reminder message" + ("" if setup.message_required else " (optional)"),
            style=discord.TextStyle.paragraph,
            required=setup.message_required,
            default=setup.message or None,
            max_length=1000,
            placeholder="What should the reminder say?",
        )
        self.when_in = discord.ui.TextInput(
            label="When? MM/DD/YYYY or HH:MM timer",
            default=setup.when_raw or None,
            max_length=20,
            placeholder="12/25/2026 18:30   —or—   02:30",
        )
        self.repeat_in = discord.ui.TextInput(
            label="Repeat (optional)",
            required=False,
            default=setup.repeat_raw or None,
            max_length=20,
            placeholder="daily / weekly / every 2 days / 12h / blank = once",
        )
        self.add_item(self.msg_in)
        self.add_item(self.when_in)
        self.add_item(self.repeat_in)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        v = self.setup
        v.message = (self.msg_in.value or "").strip()
        v.when_raw = self.when_in.value.strip()
        v.repeat_raw = (self.repeat_in.value or "").strip()
        v.error, v.parsed, v.keep_due, v.interval_seconds = None, None, None, None
        if v.message_required and not v.message:
            v.error = "Write a message for the reminder."
        elif v.editing and v.when_raw == v.prefill_when:
            v.keep_due = v.editing["due_at"]
            v.interval_seconds = v.editing.get("interval_seconds")
        else:
            try:
                v.parsed = parse_when(v.when_raw, v.tz)
            except TimeParseError as e:
                v.error = str(e)
        if not v.error and v.repeat_raw:
            try:
                v.interval_seconds = parse_interval(v.repeat_raw)
            except TimeParseError as e:
                v.error = str(e)
        await v.present(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


# ───────────────────────────── setup / preview screen ─────────────────────────────
class ReminderSetupView(discord.ui.View):
    """Preview screen with user + role + channel pickers. Used for new reminders,
    editing a reminder, and attaching a timer to a task or list."""

    def __init__(
        self,
        bot,
        *,
        user_id: int,
        guild_id: int,
        channel_id: Optional[int],
        tz: str,
        tz_set: bool,
        notify_ids: list[int],
        title: str,
        message_required: bool = True,
        link_list_id: Optional[int] = None,
        link_task_id: Optional[int] = None,
        editing: Optional[dict] = None,
        message: str = "",
        when_raw: str = "",
        context: str = "",
        notify_roles: Optional[list[int]] = None,
        ping_everyone: bool = False,
        interval_seconds: Optional[int] = None,
        allow_everyone: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.bot, self.user_id, self.guild_id = bot, user_id, guild_id
        self.channel_id, self.tz, self.tz_set = channel_id, tz, tz_set
        self.notify_ids = list(notify_ids)
        self.notify_roles = list(notify_roles or [])
        self.ping_everyone = ping_everyone
        self.allow_everyone = allow_everyone
        self.title, self.context = title, context
        self.message_required = message_required
        self.link_list_id, self.link_task_id, self.editing = link_list_id, link_task_id, editing
        self.message, self.when_raw, self.prefill_when = message, when_raw, when_raw
        self.repeat_raw = format_interval(interval_seconds) if interval_seconds else ""
        self.interval_seconds = interval_seconds
        self.parsed: Optional[ParsedWhen] = None
        self.keep_due: Optional[int] = editing["due_at"] if editing else None
        self.error: Optional[str] = None
        self.sent = False
        self._build()

    @property
    def ready(self) -> bool:
        return (
            self.error is None
            and (self.parsed is not None or self.keep_due is not None)
            and (bool(self.message) or not self.message_required)
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This scroll isn't yours.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await report_error(interaction, error)

    # ---- rendering ----
    def _build(self) -> None:
        self.clear_items()
        self.user_select = discord.ui.UserSelect(
            placeholder="Who should be pinged? (optional)",
            min_values=0,
            max_values=MAX_PINGED,
            row=0,
            default_values=user_defaults(self.notify_ids),
        )
        self.user_select.callback = self.on_users
        self.role_select = discord.ui.RoleSelect(
            placeholder="Roles to ping? (optional)",
            min_values=0,
            max_values=MAX_ROLE_PINGS,
            row=1,
            default_values=role_defaults(self.notify_roles),
        )
        self.role_select.callback = self.on_roles
        self.channel_select = discord.ui.ChannelSelect(
            placeholder="Which channel?",
            min_values=1,
            max_values=1,
            row=2,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=channel_defaults(self.channel_id),
        )
        self.channel_select.callback = self.on_channel
        edit = discord.ui.Button(
            label="Edit message & time",
            emoji="✏️",
            row=3,
            style=discord.ButtonStyle.primary,
        )
        edit.callback = self.on_edit
        save = discord.ui.Button(
            label="Save",
            emoji="✅",
            row=3,
            disabled=not self.ready,
            style=discord.ButtonStyle.success,
        )
        save.callback = self.on_save
        cancel = discord.ui.Button(label="Cancel", row=3, style=discord.ButtonStyle.danger)
        cancel.callback = self.on_cancel
        for item in (
            self.user_select,
            self.role_select,
            self.channel_select,
            edit,
            save,
            cancel,
        ):
            self.add_item(item)
        if self.allow_everyone:
            everyone_btn = discord.ui.Button(
                label="@everyone ON" if self.ping_everyone else "@everyone OFF",
                emoji="📢",
                row=4,
                style=(
                    discord.ButtonStyle.danger
                    if self.ping_everyone
                    else discord.ButtonStyle.secondary
                ),
            )
            everyone_btn.callback = self.on_everyone
            self.add_item(everyone_btn)

    def embed(self) -> discord.Embed:
        e = discord.Embed(
            title=f"⏰ {self.title}", colour=GOLD, description=self.context or None
        )
        if self.message:
            e.add_field(name="Message", value=trim(self.message, 500), inline=False)
        else:
            e.add_field(
                name="Message",
                inline=False,
                value=(
                    "*default message*"
                    if not self.message_required
                    else "*not set yet*"
                ),
            )
        if self.keep_due is not None:
            when = f"📅 {when_text(self.keep_due)}"
        elif self.parsed is not None:
            when = describe_when(self.parsed, self.when_raw, "Save")
        else:
            when = "*not set yet*"
        e.add_field(name="When", value=when, inline=False)
        e.add_field(
            name="Repeat",
            value=format_interval(self.interval_seconds),
            inline=False,
        )
        e.add_field(
            name="Channel",
            value=f"<#{self.channel_id}>" if self.channel_id else "—",
        )
        ping_parts = []
        if self.ping_everyone:
            ping_parts.append("@everyone")
        if self.notify_roles:
            ping_parts.append(role_pings(self.notify_roles))
        if self.notify_ids:
            ping_parts.append(pings(self.notify_ids))
        e.add_field(
            name="Pings",
            value=" · ".join(ping_parts) if ping_parts else "Nobody — message only",
        )
        if self.error:
            e.add_field(name="⚠️ Problem", value=self.error, inline=False)
        foot = f"Typed dates are read as {self.tz}"
        if not self.tz_set:
            foot += " • set yours with /tasklist timezone"
        e.set_footer(text=foot)
        return e

    async def present(self, interaction: discord.Interaction) -> None:
        """Send the screen the first time, edit it afterwards."""
        self._build()
        if self.sent:
            await interaction.response.edit_message(embed=self.embed(), view=self)
        else:
            self.sent = True
            await interaction.response.send_message(
                embed=self.embed(), view=self, ephemeral=True
            )

    # ---- callbacks ----
    async def on_users(self, interaction: discord.Interaction) -> None:
        self.notify_ids = [u.id for u in self.user_select.values if not u.bot][:MAX_PINGED]
        await self.present(interaction)

    async def on_roles(self, interaction: discord.Interaction) -> None:
        self.notify_roles = [r.id for r in self.role_select.values][:MAX_ROLE_PINGS]
        await self.present(interaction)

    async def on_channel(self, interaction: discord.Interaction) -> None:
        self.channel_id = self.channel_select.values[0].id
        await self.present(interaction)

    async def on_everyone(self, interaction: discord.Interaction) -> None:
        self.ping_everyone = not self.ping_everyone
        await self.present(interaction)

    async def on_edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ReminderModal(self))

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="🗑️ Cancelled.", embed=None, view=None
        )

    async def on_save(self, interaction: discord.Interaction) -> None:
        now = int(time.time())
        if self.keep_due is not None:
            kind, due = self.editing["kind"], self.keep_due
        else:
            kind = self.parsed.kind
            due = now + self.parsed.seconds if kind == "timer" else self.parsed.due_at
            if due <= now:
                self.parsed, self.error = None, "That moment has already passed — set a new time."
                return await self.present(interaction)

        channel = (
            interaction.guild.get_channel_or_thread(self.channel_id)
            if self.channel_id
            else None
        )
        if channel is None:
            self.error = "I can't see that channel — pick another."
            return await self.present(interaction)
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            self.error = (
                f"I need View Channel, Send Messages and Embed Links in {channel.mention}."
            )
            return await self.present(interaction)
        if self.ping_everyone and not perms.mention_everyone:
            self.error = f"I need the **Mention Everyone** permission in {channel.mention}."
            return await self.present(interaction)

        data = dict(
            message=self.message,
            kind=kind,
            due_at=due,
            notify_ids=self.notify_ids,
            notify_roles=self.notify_roles,
            ping_everyone=self.ping_everyone,
            interval_seconds=self.interval_seconds,
        )
        db = self.bot.db
        if self.editing:
            await db.update_reminder(self.editing["id"], channel_id=self.channel_id, **data)
        elif self.link_list_id is not None:
            await db.set_timer(
                self.guild_id,
                self.user_id,
                self.channel_id,
                data,
                self.link_list_id,
                self.link_task_id,
            )
        else:
            await db.add_reminder(self.guild_id, self.user_id, self.channel_id, data)

        e = discord.Embed(
            title="⏰ Reminder saved",
            colour=GOLD,
            description=self.message or self.context or None,
        )
        e.add_field(name="When", value=when_text(due), inline=False)
        e.add_field(name="Repeat", value=format_interval(self.interval_seconds))
        e.add_field(name="Channel", value=f"<#{self.channel_id}>")
        ping_parts = []
        if self.ping_everyone:
            ping_parts.append("@everyone")
        if self.notify_roles:
            ping_parts.append(role_pings(self.notify_roles))
        if self.notify_ids:
            ping_parts.append(pings(self.notify_ids))
        e.add_field(
            name="Pings",
            value=" · ".join(ping_parts) if ping_parts else "Nobody — message only",
        )
        self.stop()
        await interaction.response.edit_message(content=None, embed=e, view=None)

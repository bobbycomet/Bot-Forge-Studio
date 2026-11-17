"""Task Lists & Reminders — /tasklist (static task lists you can complete, with optional timers and a live progress embed) and
/reminder (one-off and recurring reminders posted in a channel).  Timers and reminders are delivered by a background loop;
users set their timezone once with /tasklist timezone.  (Bot Forge Studio community module.)"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bsd_common import CROWN, GOLD, MAX_TASKS, report_error
from bsd_db import DuplicateListName
from bsd_timeutil import (
    ParsedWhen,
    TimeParseError,
    get_zone,
    parse_when,
    prefill_when,
    resolve_zone,
    search_zones,
    when_text,
)
from bsd_ui import (
    MAX_PINGED,
    ReminderModal,
    ReminderSetupView,
    default_channel,
    describe_when,
    pings,
    trim,
    user_defaults,
)


# ───────────────────────────── task form ─────────────────────────────
class TaskFieldsModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        name: str = "",
        description: str = "",
        timer: str = "",
        timer_msg: str = "",
        with_timer: bool = True,
    ) -> None:
        super().__init__(title=title[:45])
        self.with_timer = with_timer
        self.name_in = discord.ui.TextInput(
            label="Task name",
            default=name or None,
            max_length=100,
            placeholder="e.g. Book the venue",
        )
        self.desc_in = discord.ui.TextInput(
            label="Description (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            default=description or None,
            max_length=500,
            placeholder="Any extra details…",
        )
        self.add_item(self.name_in)
        self.add_item(self.desc_in)
        if with_timer:
            self.when_in = discord.ui.TextInput(
                label="Timer (optional): MM/DD/YYYY or HH:MM",
                required=False,
                default=timer or None,
                max_length=20,
                placeholder="Leave blank for a plain checklist task",
            )
            self.tmsg_in = discord.ui.TextInput(
                label="Timer message (optional)",
                required=False,
                default=timer_msg or None,
                max_length=300,
                placeholder="Defaults to the task name",
            )
            self.add_item(self.when_in)
            self.add_item(self.tmsg_in)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


# ───────────────────────────── /tasklist create wizard ─────────────────────────────
class CreateListModal(discord.ui.Modal, title="Draft a task list"):
    list_name = discord.ui.TextInput(
        label="Name this list", placeholder="e.g. Wedding prep", max_length=80
    )
    task_count = discord.ui.TextInput(
        label=f"How many tasks? (1-{MAX_TASKS})", placeholder="5", max_length=2
    )
    countdown = discord.ui.TextInput(
        label="Whole-list countdown (optional)",
        required=False,
        max_length=20,
        placeholder="MM/DD/YYYY or HH:MM — leave blank for none",
    )
    countdown_msg = discord.ui.TextInput(
        label="Countdown message (optional)",
        required=False,
        max_length=300,
        placeholder="What to say when the countdown ends",
    )

    def __init__(self, cog: "TaskLists") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.list_name.value.strip()
        try:
            count = int(self.task_count.value.strip())
        except ValueError:
            count = 0
        say = interaction.response.send_message
        if not name:
            return await say("❌ The list needs a name.", ephemeral=True)
        if not 1 <= count <= MAX_TASKS:
            return await say(f"❌ Choose between 1 and {MAX_TASKS} tasks.", ephemeral=True)
        db = self.cog.bot.db
        if await db.list_name_taken(interaction.guild_id, interaction.user.id, name):
            return await say(
                f"❌ You already have a list called **{name}** in this server.", ephemeral=True
            )

        tz = await db.get_timezone(interaction.user.id)
        list_timer = None
        cw = self.countdown.value.strip()
        if cw:
            try:
                list_timer = dict(
                    parsed=parse_when(cw, tz or "UTC"),
                    raw=cw,
                    message=self.countdown_msg.value.strip(),
                )
            except TimeParseError as e:
                return await say(f"❌ List countdown: {e}", ephemeral=True)

        view = WizardView(
            self.cog,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=await default_channel(self.cog.bot, interaction),
            name=name,
            count=count,
            tz=tz or "UTC",
            tz_set=tz is not None,
            list_timer=list_timer,
        )
        await say(embed=view.embed(), view=view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


@dataclass
class DraftTask:
    name: str = ""
    description: str = ""
    when_raw: str = ""
    timer_message: str = ""
    parsed: Optional[ParsedWhen] = None
    ok: bool = False
    notify_ids: list[int] = field(default_factory=list)


class WizardTaskModal(TaskFieldsModal):
    def __init__(self, wizard: "WizardView") -> None:
        t = wizard.current
        super().__init__(
            title=f"Task {wizard.index + 1} of {len(wizard.tasks)}",
            name=t.name,
            description=t.description,
            timer=t.when_raw,
            timer_msg=t.timer_message,
        )
        self.wizard = wizard

    async def on_submit(self, interaction: discord.Interaction) -> None:
        w, t = self.wizard, self.wizard.current
        t.name = self.name_in.value.strip()
        t.description = (self.desc_in.value or "").strip()
        t.when_raw = (self.when_in.value or "").strip()
        t.timer_message = (self.tmsg_in.value or "").strip()
        t.parsed, t.ok, w.error = None, False, None
        if not t.name:
            w.error = "Give the task a name."
        elif t.when_raw:
            try:
                t.parsed = parse_when(t.when_raw, w.tz)
            except TimeParseError as e:
                w.error = f"Timer: {e}"
        t.ok = w.error is None
        await w.refresh(interaction)


class WizardView(discord.ui.View):
    """Ephemeral step-by-step form: one screen per task."""

    def __init__(
        self,
        cog: "TaskLists",
        *,
        user_id: int,
        guild_id: int,
        channel_id: Optional[int],
        name: str,
        count: int,
        tz: str,
        tz_set: bool,
        list_timer: Optional[dict],
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id, self.guild_id, self.channel_id = user_id, guild_id, channel_id
        self.name, self.tz, self.tz_set, self.list_timer = name, tz, tz_set, list_timer
        self.tasks = [DraftTask(notify_ids=[user_id]) for _ in range(count)]
        self.index = 0
        self.error: Optional[str] = None
        self._build()

    @property
    def current(self) -> DraftTask:
        return self.tasks[self.index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This scroll isn't yours.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await report_error(interaction, error)

    def _build(self) -> None:
        self.clear_items()
        last = self.index == len(self.tasks) - 1
        t = self.current
        if t.parsed is not None:
            self.select = discord.ui.UserSelect(
                placeholder="Who to ping when this task's timer fires",
                min_values=0,
                max_values=MAX_PINGED,
                row=0,
                default_values=user_defaults(t.notify_ids),
            )
            self.select.callback = self.on_select
            self.add_item(self.select)
        back = discord.ui.Button(
            label="Back",
            emoji="⬅️",
            row=1,
            style=discord.ButtonStyle.secondary,
            disabled=self.index == 0,
        )
        back.callback = self.on_back
        edit = discord.ui.Button(
            label="Edit details" if t.ok else "Fill in details",
            emoji="✏️",
            row=1,
            style=discord.ButtonStyle.primary,
        )
        edit.callback = self.on_edit
        nxt = discord.ui.Button(
            label="Done" if last else "Continue",
            emoji="✅" if last else "➡️",
            row=1,
            style=discord.ButtonStyle.success,
            disabled=not t.ok,
        )
        nxt.callback = self.on_done if last else self.on_next
        cancel = discord.ui.Button(label="Cancel", row=2, style=discord.ButtonStyle.danger)
        cancel.callback = self.on_cancel
        for b in (back, edit, nxt, cancel):
            self.add_item(b)

    def embed(self) -> discord.Embed:
        n, i, t = len(self.tasks), self.index, self.current
        progress = "".join(
            "✅" if x.ok else ("➡️" if k == i else "⬜") for k, x in enumerate(self.tasks)
        )
        desc = f"**Task {i + 1} of {n}**\n{progress}"
        if self.list_timer:
            desc += "\n⏳ List countdown: " + describe_when(
                self.list_timer["parsed"], self.list_timer["raw"], "Done"
            )
        e = discord.Embed(title=f"{CROWN} {self.name}", colour=GOLD, description=desc)
        e.add_field(name="Task", value=t.name or "*not set yet*", inline=False)
        if t.description:
            e.add_field(name="Description", value=trim(t.description, 300), inline=False)
        if t.parsed:
            e.add_field(
                name="Timer",
                value=describe_when(t.parsed, t.when_raw, "Done"),
                inline=False,
            )
            e.add_field(
                name="Pings",
                value=pings(t.notify_ids, "Nobody — message only"),
                inline=False,
            )
        else:
            e.add_field(name="Timer", value="None — a plain checklist task", inline=False)
        if self.error:
            e.add_field(name="⚠️ Problem", value=self.error, inline=False)
        foot = f"Typed dates are read as {self.tz}"
        if not self.tz_set:
            foot += " • set yours with /tasklist timezone"
        e.set_footer(text=foot)
        return e

    async def refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_select(self, interaction: discord.Interaction) -> None:
        self.current.notify_ids = [u.id for u in self.select.values if not u.bot][:MAX_PINGED]
        self.error = None
        await self.refresh(interaction)

    async def on_back(self, interaction: discord.Interaction) -> None:
        self.index, self.error = max(0, self.index - 1), None
        await self.refresh(interaction)

    async def on_next(self, interaction: discord.Interaction) -> None:
        self.index, self.error = min(len(self.tasks) - 1, self.index + 1), None
        await self.refresh(interaction)

    async def on_edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(WizardTaskModal(self))

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="🗑️ Draft discarded.", embed=None, view=None
        )

    @staticmethod
    def _resolve(p: ParsedWhen, now: int) -> tuple[str, int]:
        return p.kind, (now + p.seconds if p.kind == "timer" else p.due_at)

    async def on_done(self, interaction: discord.Interaction) -> None:
        now = int(time.time())
        rows = []
        for idx, t in enumerate(self.tasks):
            if not t.ok:
                self.index, self.error = idx, f"Task {idx + 1} still needs details."
                return await self.refresh(interaction)
            row = dict(name=t.name, description=t.description, reminder=None)
            if t.parsed:
                kind, due = self._resolve(t.parsed, now)
                if due <= now:
                    t.ok, t.parsed = False, None
                    self.index, self.error = (
                        idx,
                        "That timer's date has already passed — set a new one.",
                    )
                    return await self.refresh(interaction)
                row["reminder"] = dict(
                    message=t.timer_message,
                    kind=kind,
                    due_at=due,
                    notify_ids=t.notify_ids,
                )
            rows.append(row)

        list_reminder = None
        if self.list_timer:
            kind, due = self._resolve(self.list_timer["parsed"], now)
            if due <= now:
                self.stop()
                return await interaction.response.edit_message(
                    content="❌ The list countdown date has already passed. Run /tasklist create again.",
                    embed=None,
                    view=None,
                )
            list_reminder = dict(
                message=self.list_timer["message"],
                kind=kind,
                due_at=due,
                notify_ids=[self.user_id],
            )

        try:
            await self.cog.bot.db.create_list(
                self.guild_id, self.user_id, self.channel_id, self.name, rows, list_reminder
            )
        except DuplicateListName:
            self.stop()
            return await interaction.response.edit_message(
                content=(
                    f"❌ You already have a list called **{self.name}**. "
                    "Run /tasklist create again with another name."
                ),
                embed=None,
                view=None,
            )

        lines = []
        for i, r in enumerate(rows, 1):
            line = f"`{i:>2}.` ☐ **{trim(r['name'], 60)}**"
            if r["reminder"]:
                line += f" ⏱ <t:{r['reminder']['due_at']}:F>"
            lines.append(line)
        if list_reminder:
            lines.append(f"\n⏳ List countdown ends {when_text(list_reminder['due_at'])}")
        e = discord.Embed(
            title=f"📜 List created: {self.name}",
            colour=GOLD,
            description="\n".join(lines)[:4000],
        )
        e.set_footer(
            text=(
                "Complete tasks with /tasklist complete • timers post in "
                f"#{interaction.guild.get_channel(self.channel_id) or 'the channel'}"
            )
        )
        self.stop()
        await interaction.response.edit_message(content=None, embed=e, view=None)


# ───────────────────────────── import modal ─────────────────────────────
class ImportListModal(discord.ui.Modal, title="Import tasks from text"):
    list_name = discord.ui.TextInput(
        label="Name this list", placeholder="e.g. Shopping", max_length=80
    )
    body = discord.ui.TextInput(
        label="One task per line",
        style=discord.TextStyle.paragraph,
        placeholder="Book venue\nSend invitations\nOrder cake",
        max_length=2000,
    )

    def __init__(self, cog: "TaskLists") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.list_name.value.strip()
        lines = [ln.strip() for ln in self.body.value.splitlines() if ln.strip()]
        # Strip leading numbers / bullets
        cleaned = []
        for ln in lines:
            for prefix in ("- ", "* ", "• "):
                if ln.startswith(prefix):
                    ln = ln[len(prefix):]
                    break
            if ln and ln[0].isdigit():
                parts = ln.split(".", 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    ln = parts[1].strip()
            if ln:
                cleaned.append(ln)
        if not name:
            return await interaction.response.send_message(
                "❌ The list needs a name.", ephemeral=True
            )
        if not cleaned:
            return await interaction.response.send_message(
                "❌ Paste at least one task (one per line).", ephemeral=True
            )
        if len(cleaned) > MAX_TASKS:
            return await interaction.response.send_message(
                f"❌ A list can hold at most {MAX_TASKS} tasks (you pasted {len(cleaned)}).",
                ephemeral=True,
            )
        db = self.cog.bot.db
        if await db.list_name_taken(interaction.guild_id, interaction.user.id, name):
            return await interaction.response.send_message(
                f"❌ You already have a list called **{name}**.", ephemeral=True
            )
        tasks = [dict(name=n, description="", reminder=None) for n in cleaned]
        await db.create_list(
            interaction.guild_id,
            interaction.user.id,
            await default_channel(self.cog.bot, interaction),
            name,
            tasks,
        )
        await interaction.response.send_message(
            f"✅ Created **{name}** with {len(tasks)} tasks. View with `/tasklist view {name}`.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


# ───────────────────────────── add / edit task ─────────────────────────────
class AddTaskModal(TaskFieldsModal):
    def __init__(self, cog: "TaskLists", lst: dict, tz: str) -> None:
        super().__init__(title="Add a task")
        self.cog, self.lst, self.tz = cog, lst, tz

    async def on_submit(self, interaction: discord.Interaction) -> None:
        db, say = self.cog.bot.db, interaction.response.send_message
        name = self.name_in.value.strip()
        if not name:
            return await say("❌ The task needs a name.", ephemeral=True)
        when = (self.when_in.value or "").strip()
        parsed = None
        if when:
            try:
                parsed = parse_when(when, self.tz)
            except TimeParseError as e:
                return await say(
                    f"❌ Timer: {e}\nNothing was added — run the command again to retry.",
                    ephemeral=True,
                )
        if await db.count_tasks(self.lst["id"]) >= MAX_TASKS:
            return await say(f"❌ A list can hold at most {MAX_TASKS} tasks.", ephemeral=True)
        task_id = await db.add_task(self.lst["id"], name, (self.desc_in.value or "").strip())
        task = await db.get_task(task_id)
        msg = f"✅ Added **#{task['position']} {name}** to **{self.lst['name']}**."
        if parsed:
            # Open the full setup view so the user can pick pings / channel / repeat
            view = ReminderSetupView(
                self.cog.bot,
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=await default_channel(self.cog.bot, interaction),
                tz=self.tz,
                tz_set=True,
                notify_ids=[interaction.user.id],
                title="Task timer",
                message_required=False,
                link_list_id=self.lst["id"],
                link_task_id=task_id,
                message=(self.tmsg_in.value or "").strip(),
                when_raw=when,
                context=f"{msg}\nTask #{task['position']}: {name} ({self.lst['name']})",
            )
            view.parsed = parsed
            await view.present(interaction)
            return
        await say(msg, ephemeral=True)
        await self.cog._refresh_progress(self.lst["id"])


class EditTaskModal(TaskFieldsModal):
    def __init__(self, cog: "TaskLists", task: dict) -> None:
        super().__init__(
            title=f"Edit task #{task['position']}",
            name=task["name"],
            description=task["description"],
            with_timer=False,
        )
        self.cog, self.task = cog, task

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.name_in.value.strip()
        if not name:
            return await interaction.response.send_message(
                "❌ The task needs a name.", ephemeral=True
            )
        await self.cog.bot.db.update_task(
            self.task["id"], name, (self.desc_in.value or "").strip()
        )
        await interaction.response.send_message(
            f"✅ Task **#{self.task['position']}** updated.", ephemeral=True
        )
        await self.cog._refresh_progress(self.task["list_id"])


# ───────────────────────────── rename / delete / archive ─────────────────────────────
class RenameListModal(discord.ui.Modal, title="Rename list"):
    def __init__(self, db, lst: dict) -> None:
        super().__init__()
        self.db, self.lst = db, lst
        self.name_in = discord.ui.TextInput(
            label="List name", default=lst["name"], max_length=80
        )
        self.add_item(self.name_in)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.name_in.value.strip()
        if not name:
            return await interaction.response.send_message(
                "❌ The list needs a name.", ephemeral=True
            )
        try:
            await self.db.rename_list(self.lst["id"], name)
        except DuplicateListName:
            return await interaction.response.send_message(
                f"❌ A list called **{name}** already exists.", ephemeral=True
            )
        await interaction.response.send_message(f"✅ Renamed to **{name}**.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, cog: "TaskLists", lst: dict, user_id: int) -> None:
        super().__init__(timeout=60)
        self.cog, self.lst, self.user_id = cog, lst, user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Delete forever", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.bot.db.delete_list(self.lst["id"], interaction.user.id)
        self.stop()
        await interaction.response.edit_message(
            content=f"🗑️ **{self.lst['name']}** and its timers were deleted.", view=None
        )

    @discord.ui.button(label="Keep it", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Phew — the list is safe.", view=None
        )


# ───────────────────────────── the cog ─────────────────────────────
@app_commands.guild_only()
class TaskLists(
    commands.GroupCog,
    group_name="tasklist",
    group_description="Task lists you can complete, with optional timers",
):
    def __init__(self, bot) -> None:
        self.bot = bot
        super().__init__()

    # ---- permissions / lookups ----
    @staticmethod
    def _can_manage(interaction: discord.Interaction, lst: dict) -> bool:
        return lst["owner_id"] == interaction.user.id or interaction.permissions.manage_guild

    @staticmethod
    async def _say(interaction: discord.Interaction, msg: str) -> None:
        await interaction.response.send_message(msg, ephemeral=True)

    async def _get_list(
        self, interaction: discord.Interaction, raw: Optional[str], *, allow_archived: bool = False
    ) -> Optional[dict]:
        db, lst = self.bot.db, None
        if raw and raw.isdigit():
            lst = await db.get_list(int(raw))
        if lst is None and raw:
            lst = await db.find_list_by_name(interaction.guild_id, interaction.user.id, raw)
            if lst is None and allow_archived:
                # try including archived by id only
                pass
        if lst is None or lst["guild_id"] != interaction.guild_id:
            await self._say(
                interaction, "❌ I couldn't find that list — pick one from the suggestions."
            )
            return None
        if not allow_archived and lst.get("archived"):
            await self._say(
                interaction, "❌ That list is archived. Use `/tasklist unarchive` first."
            )
            return None
        if not self._can_manage(interaction, lst):
            await self._say(interaction, "❌ That list belongs to someone else.")
            return None
        return lst

    async def _get_task(
        self, interaction: discord.Interaction, lst: dict, number: int
    ) -> Optional[dict]:
        task = await self.bot.db.get_task_by_position(lst["id"], number)
        if task is None:
            await self._say(interaction, f"❌ **{lst['name']}** has no task #{number}.")
        return task

    async def _refresh_progress(self, list_id: int) -> None:
        """Edit the live progress message if one is attached to this list."""
        lst = await self.bot.db.get_list(list_id)
        if not lst or not lst.get("progress_message_id") or not lst.get("progress_channel_id"):
            return
        try:
            channel = self.bot.get_channel(lst["progress_channel_id"])
            if channel is None:
                channel = await self.bot.fetch_channel(lst["progress_channel_id"])
            msg = await channel.fetch_message(lst["progress_message_id"])
            embed = await self._build_list_embed(lst)
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Message gone or no perms — clear the pointer
            await self.bot.db.set_progress_message(list_id, None, None)

    async def _build_list_embed(self, lst: dict) -> discord.Embed:
        tasks = await self.bot.db.tasks_for(lst["id"])
        rems = await self.bot.db.reminders_for_list(lst["id"])
        by_task = {r["task_id"]: r for r in rems if r["task_id"]}
        countdown = next((r for r in rems if r["task_id"] is None), None)
        lines = []
        if countdown:
            lines.append(f"⏳ **Countdown:** {when_text(countdown['due_at'])}\n")
        for t in tasks:
            done = t["completed_at"] is not None
            name = trim(t["name"], 60)
            line = (
                f"`{t['position']:>2}.` {'✅' if done else '☐'} "
                + (f"~~{name}~~" if done else f"**{name}**")
            )
            r = by_task.get(t["id"])
            if r:
                line += f" ⏱ <t:{r['due_at']}:R>"
            lines.append(line)
        done_n = sum(1 for t in tasks if t["completed_at"])
        e = discord.Embed(
            title=f"{CROWN} {lst['name']}",
            colour=GOLD,
            description=("\n".join(lines) or "*No tasks*")[:4000],
        )
        e.set_footer(text=f"{done_n}/{len(tasks)} complete")
        return e

    # ---- autocomplete ----
    async def list_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        mgr = interaction.permissions.manage_guild
        rows = await self.bot.db.lists_for(
            interaction.guild_id, None if mgr else interaction.user.id
        )
        cur, out = current.lower(), []
        for r in rows:
            label = r["name"] + (
                f" (#{r['id']})" if r["owner_id"] != interaction.user.id else ""
            )
            if cur in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=str(r["id"])))
        return out[:25]

    async def archived_list_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        mgr = interaction.permissions.manage_guild
        rows = await self.bot.db.lists_for(
            interaction.guild_id,
            None if mgr else interaction.user.id,
            include_archived=True,
        )
        rows = [r for r in rows if r.get("archived")]
        cur, out = current.lower(), []
        for r in rows:
            label = r["name"] + " (archived)"
            if cur in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=str(r["id"])))
        return out[:25]

    async def _task_choices(
        self,
        interaction: discord.Interaction,
        current: str,
        completed: Optional[bool],
    ) -> list[app_commands.Choice[int]]:
        raw = getattr(interaction.namespace, "list_name", None)
        if not raw:
            return []
        db = self.bot.db
        lst = (
            await db.get_list(int(raw))
            if str(raw).isdigit()
            else await db.find_list_by_name(
                interaction.guild_id, interaction.user.id, str(raw)
            )
        )
        if (
            not lst
            or lst["guild_id"] != interaction.guild_id
            or not self._can_manage(interaction, lst)
        ):
            return []
        cur, out = current.lower(), []
        for t in await db.tasks_for(lst["id"]):
            done = t["completed_at"] is not None
            if completed is not None and done != completed:
                continue
            label = f"{t['position']}. {'✅ ' if done else ''}{t['name']}"
            if cur in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=t["position"]))
        return out[:25]

    async def any_task_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._task_choices(interaction, current, None)

    async def open_task_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._task_choices(interaction, current, False)

    async def done_task_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._task_choices(interaction, current, True)

    async def zone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=z, value=z) for z in search_zones(current)]

    # ---- lists ----
    @app_commands.command(name="create", description="Draft a new task list (timers optional).")
    async def create(self, interaction: discord.Interaction) -> None:
        # Footer on the wizard already nudges users who haven't set a timezone.
        await interaction.response.send_modal(CreateListModal(self))

    @app_commands.command(
        name="import", description="Create a list by pasting tasks (one per line)."
    )
    async def import_(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ImportListModal(self))

    @app_commands.command(
        name="view", description="See your lists, or open one to see its tasks."
    )
    @app_commands.describe(list_name="The list to open (leave empty to see all your lists)")
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def view(
        self, interaction: discord.Interaction, list_name: Optional[str] = None
    ) -> None:
        db = self.bot.db
        if list_name is None:
            mgr = interaction.permissions.manage_guild
            rows = await db.lists_for(
                interaction.guild_id, None if mgr else interaction.user.id
            )
            if not rows:
                return await self._say(
                    interaction,
                    "You have no lists yet — start one with **/tasklist create** or **/tasklist import**.",
                )
            lines = []
            for r in rows[:25]:
                line = f"**{r['name']}** — {r['done_count']}/{r['task_count']} complete"
                if r["owner_id"] != interaction.user.id:
                    line += f" • by <@{r['owner_id']}>"
                lines.append(line)
            if len(rows) > 25:
                lines.append(f"…and {len(rows) - 25} more")
            e = discord.Embed(
                title=f"{CROWN} Your task lists",
                colour=GOLD,
                description="\n".join(lines),
            )
            return await interaction.response.send_message(embed=e, ephemeral=True)

        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        e = await self._build_list_embed(lst)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(
        name="publish",
        description="Post a live progress embed that updates when tasks are completed.",
    )
    @app_commands.describe(
        list_name="The list to publish",
        channel="Channel for the public progress message (defaults to current)",
    )
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def publish(
        self,
        interaction: discord.Interaction,
        list_name: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        ch = channel or interaction.channel
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return await self._say(interaction, "❌ Pick a text channel.")
        perms = ch.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await self._say(
                interaction,
                f"❌ I need View Channel, Send Messages and Embed Links in {ch.mention}.",
            )
        embed = await self._build_list_embed(lst)
        msg = await ch.send(embed=embed)
        await self.bot.db.set_progress_message(lst["id"], ch.id, msg.id)
        await self._say(
            interaction,
            f"✅ Live progress for **{lst['name']}** posted in {ch.mention}. "
            "It will update when tasks are completed or reopened.",
        )

    @app_commands.command(name="edit-list", description="Rename one of your lists.")
    @app_commands.describe(list_name="The list to rename")
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def edit_list(self, interaction: discord.Interaction, list_name: str) -> None:
        lst = await self._get_list(interaction, list_name)
        if lst:
            await interaction.response.send_modal(RenameListModal(self.bot.db, lst))

    @app_commands.command(
        name="archive", description="Archive a finished list (hides it from normal views)."
    )
    @app_commands.describe(list_name="The list to archive")
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def archive(self, interaction: discord.Interaction, list_name: str) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        await self.bot.db.set_archived(lst["id"], True, interaction.user.id)
        await self._say(
            interaction,
            f"📦 **{lst['name']}** archived. Unarchive later with `/tasklist unarchive`.",
        )

    @app_commands.command(name="unarchive", description="Restore an archived list.")
    @app_commands.describe(list_name="The archived list to restore")
    @app_commands.autocomplete(list_name=archived_list_autocomplete)
    async def unarchive(self, interaction: discord.Interaction, list_name: str) -> None:
        lst = await self._get_list(interaction, list_name, allow_archived=True)
        if not lst:
            return
        if not lst.get("archived"):
            return await self._say(interaction, "That list isn't archived.")
        await self.bot.db.set_archived(lst["id"], False, interaction.user.id)
        await self._say(interaction, f"📂 **{lst['name']}** restored.")

    @app_commands.command(
        name="delete-list", description="Delete an entire list, its tasks, and their timers."
    )
    @app_commands.describe(list_name="The list to delete")
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def delete_list(self, interaction: discord.Interaction, list_name: str) -> None:
        lst = await self._get_list(interaction, list_name, allow_archived=True)
        if lst:
            await interaction.response.send_message(
                f"Really delete **{lst['name']}** and all of its tasks and timers?",
                view=ConfirmDeleteView(self, lst, interaction.user.id),
                ephemeral=True,
            )

    # ---- tasks ----
    @app_commands.command(name="add-task", description="Add another task to a list.")
    @app_commands.describe(list_name="The list to add to")
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def add_task(self, interaction: discord.Interaction, list_name: str) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        if await self.bot.db.count_tasks(lst["id"]) >= MAX_TASKS:
            return await self._say(
                interaction, f"❌ A list can hold at most {MAX_TASKS} tasks."
            )
        tz = await self.bot.db.get_timezone(interaction.user.id) or "UTC"
        await interaction.response.send_modal(AddTaskModal(self, lst, tz))

    @app_commands.command(name="edit-task", description="Edit a task's name or description.")
    @app_commands.describe(
        list_name="The list the task is in", number="The task's number in that list"
    )
    @app_commands.autocomplete(list_name=list_autocomplete, number=any_task_autocomplete)
    async def edit_task(
        self, interaction: discord.Interaction, list_name: str, number: int
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        task = await self._get_task(interaction, lst, number) if lst else None
        if task:
            await interaction.response.send_modal(EditTaskModal(self, task))

    @app_commands.command(
        name="remove-task", description="Remove a task (and its timer) from a list."
    )
    @app_commands.describe(
        list_name="The list the task is in", number="The task's number in that list"
    )
    @app_commands.autocomplete(list_name=list_autocomplete, number=any_task_autocomplete)
    async def remove_task(
        self, interaction: discord.Interaction, list_name: str, number: int
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        task = await self._get_task(interaction, lst, number) if lst else None
        if task:
            await self.bot.db.delete_task(task["id"])
            await self._say(
                interaction,
                f"🗑️ Removed **{task['name']}** from **{lst['name']}**. Later tasks were renumbered.",
            )
            await self._refresh_progress(lst["id"])

    @app_commands.command(name="complete", description="Mark a task as complete.")
    @app_commands.describe(
        list_name="The list the task is in", number="The task's number in that list"
    )
    @app_commands.autocomplete(list_name=list_autocomplete, number=open_task_autocomplete)
    async def complete(
        self, interaction: discord.Interaction, list_name: str, number: int
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        task = await self._get_task(interaction, lst, number) if lst else None
        if not task:
            return
        if task["completed_at"]:
            return await self._say(
                interaction, f"Task **#{number} {task['name']}** is already complete."
            )
        res = await self.bot.db.set_task_done(task["id"], interaction.user.id, True)
        msg = (
            f"✅ Completed **#{number} {task['name']}** in **{lst['name']}** "
            f"({res['done']}/{res['total']})."
        )
        if res["task_timer_cancelled"]:
            msg += "\n⏱ Its one-shot timer was cancelled."
        if res["all_done"]:
            msg += "\n🎉 Every task in this list is done!"
            if res["list_timer_cancelled"]:
                msg += " The list countdown was cancelled."
        await self._say(interaction, msg)
        await self._refresh_progress(lst["id"])

    @app_commands.command(
        name="complete-bulk",
        description="Mark several tasks complete (comma-separated numbers).",
    )
    @app_commands.describe(
        list_name="The list", numbers="e.g. 1,3,5 or 1-4"
    )
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def complete_bulk(
        self, interaction: discord.Interaction, list_name: str, numbers: str
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        positions = self._parse_numbers(numbers)
        if not positions:
            return await self._say(
                interaction, "❌ Give task numbers like `1,3,5` or `1-4`."
            )
        res = await self.bot.db.bulk_set_done(
            lst["id"], positions, interaction.user.id, True
        )
        msg = (
            f"✅ Marked {res['changed']} task(s) complete in **{lst['name']}** "
            f"({res['done']}/{res['total']})."
        )
        if res["all_done"]:
            msg += "\n🎉 Every task in this list is done!"
        await self._say(interaction, msg)
        await self._refresh_progress(lst["id"])

    @app_commands.command(name="reopen", description="Mark a completed task as not done.")
    @app_commands.describe(
        list_name="The list the task is in", number="The task's number in that list"
    )
    @app_commands.autocomplete(list_name=list_autocomplete, number=done_task_autocomplete)
    async def reopen(
        self, interaction: discord.Interaction, list_name: str, number: int
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        task = await self._get_task(interaction, lst, number) if lst else None
        if not task:
            return
        if not task["completed_at"]:
            return await self._say(
                interaction, f"Task **#{number} {task['name']}** isn't complete."
            )
        res = await self.bot.db.set_task_done(task["id"], interaction.user.id, False)
        await self._say(
            interaction,
            f"↩️ Reopened **#{number} {task['name']}** ({res['done']}/{res['total']}). "
            "Any timer cancelled by completing it stays cancelled — set a new one with /tasklist timer.",
        )
        await self._refresh_progress(lst["id"])

    @staticmethod
    def _parse_numbers(raw: str) -> list[int]:
        out: list[int] = []
        for part in raw.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, _, b = part.partition("-")
                try:
                    lo, hi = int(a), int(b)
                    out.extend(range(min(lo, hi), max(lo, hi) + 1))
                except ValueError:
                    continue
            else:
                try:
                    out.append(int(part))
                except ValueError:
                    continue
        return sorted(set(out))

    # ---- timers on tasks / lists ----
    @app_commands.command(
        name="timer",
        description="Add or change a timer on a task, or a countdown on the whole list.",
    )
    @app_commands.describe(
        list_name="The list",
        number="Task number (leave empty to put the countdown on the whole list)",
    )
    @app_commands.autocomplete(list_name=list_autocomplete, number=open_task_autocomplete)
    async def timer(
        self,
        interaction: discord.Interaction,
        list_name: str,
        number: Optional[int] = None,
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        task = None
        if number is not None:
            task = await self._get_task(interaction, lst, number)
            if not task:
                return
            if task["completed_at"]:
                return await self._say(interaction, "❌ That task is already complete.")
        existing = await self.bot.db.get_link_reminder(
            lst["id"], task["id"] if task else None
        )
        tz = await self.bot.db.get_timezone(interaction.user.id)
        context = (
            f"Task #{task['position']}: {task['name']} ({lst['name']})"
            if task
            else f"Whole list: {lst['name']}"
        )
        view = ReminderSetupView(
            self.bot,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=(existing["channel_id"] if existing else None)
            or await default_channel(self.bot, interaction),
            tz=tz or "UTC",
            tz_set=tz is not None,
            notify_ids=json.loads(existing["notify_ids"] or "[]")
            if existing
            else [interaction.user.id],
            notify_roles=json.loads(existing.get("notify_roles") or "[]")
            if existing
            else [],
            ping_everyone=bool(existing.get("ping_everyone")) if existing else False,
            interval_seconds=existing.get("interval_seconds") if existing else None,
            title="Task timer" if task else "List countdown",
            message_required=False,
            link_list_id=lst["id"],
            link_task_id=task["id"] if task else None,
            editing=existing,
            message=existing["message"] if existing else "",
            when_raw=prefill_when(
                existing["kind"], existing["due_at"], True, tz or "UTC"
            )
            if existing
            else "",
            context=context,
            allow_everyone=interaction.permissions.mention_everyone,
        )
        await interaction.response.send_modal(ReminderModal(view))

    @app_commands.command(
        name="clear-timer",
        description="Remove the timer from a task, or the countdown from the list.",
    )
    @app_commands.describe(
        list_name="The list",
        number="Task number (leave empty to clear the whole-list countdown)",
    )
    @app_commands.autocomplete(list_name=list_autocomplete, number=any_task_autocomplete)
    async def clear_timer(
        self,
        interaction: discord.Interaction,
        list_name: str,
        number: Optional[int] = None,
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        task = None
        if number is not None:
            task = await self._get_task(interaction, lst, number)
            if not task:
                return
        n = await self.bot.db.clear_timer(lst["id"], task["id"] if task else None)
        what = f"task #{number}" if task else "the list countdown"
        await self._say(
            interaction,
            f"🗑️ Cleared the timer on {what}." if n else f"There was no timer on {what}.",
        )

    @app_commands.command(
        name="clear-all-timers", description="Remove every timer attached to a list."
    )
    @app_commands.describe(list_name="The list")
    @app_commands.autocomplete(list_name=list_autocomplete)
    async def clear_all_timers(
        self, interaction: discord.Interaction, list_name: str
    ) -> None:
        lst = await self._get_list(interaction, list_name)
        if not lst:
            return
        n = await self.bot.db.clear_all_timers(lst["id"])
        await self._say(
            interaction,
            f"🗑️ Cleared {n} timer(s) from **{lst['name']}**."
            if n
            else f"No timers on **{lst['name']}**.",
        )

    # ---- settings / help / log ----
    @app_commands.command(
        name="timezone",
        description="Set your timezone so the dates you type are read correctly.",
    )
    @app_commands.describe(
        zone="Start typing a city, e.g. America/New_York (leave empty to see yours)"
    )
    @app_commands.autocomplete(zone=zone_autocomplete)
    async def timezone(
        self, interaction: discord.Interaction, zone: Optional[str] = None
    ) -> None:
        db = self.bot.db
        if zone is None:
            zone = await db.get_timezone(interaction.user.id)
            if not zone:
                return await self._say(
                    interaction,
                    "No timezone set yet (dates are read as UTC). "
                    "Use **/tasklist timezone** and pick your city.",
                )
        else:
            resolved = resolve_zone(zone)
            if not resolved:
                return await self._say(
                    interaction,
                    "❌ Unknown timezone — pick one from the suggestions (e.g. `America/Chicago`).",
                )
            zone = resolved
            await db.set_timezone(interaction.user.id, zone)
        now_local = datetime.now(get_zone(zone)).strftime("%a %b %d, %I:%M %p")
        await self._say(
            interaction,
            f"🕰️ Your timezone is **{zone}** (it's {now_local} there). "
            "Dates you type are read in this zone; times are always *displayed* "
            "in each viewer's own local time.",
        )

    @app_commands.command(
        name="setchannel",
        description="Set the default channel for timers and reminders (admins).",
    )
    @app_commands.describe(
        channel="Default channel (leave empty to use whichever channel a command is run in)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setchannel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not interaction.permissions.manage_guild:
            return await self._say(
                interaction, "❌ You need the **Manage Server** permission."
            )
        if channel is None:
            await self.bot.db.set_channel(interaction.guild_id, None)
            return await self._say(
                interaction,
                "✅ New timers will default to the channel where they're created.",
            )
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await self._say(
                interaction,
                f"❌ I need **View Channel**, **Send Messages** and **Embed Links** in {channel.mention}.",
            )
        await self.bot.db.set_channel(interaction.guild_id, channel.id)
        await self._say(
            interaction,
            f"✅ New timers and reminders will default to {channel.mention} "
            "(you can still pick another channel per reminder).",
        )

    @app_commands.command(
        name="setdigest",
        description="Set a channel for the daily due-soon digest (admins).",
    )
    @app_commands.describe(
        channel="Channel for the daily summary (leave empty to disable)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setdigest(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not interaction.permissions.manage_guild:
            return await self._say(
                interaction, "❌ You need the **Manage Server** permission."
            )
        if channel is None:
            await self.bot.db.set_digest_channel(interaction.guild_id, None)
            return await self._say(interaction, "✅ Daily digest disabled.")
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await self._say(
                interaction,
                f"❌ I need **View Channel**, **Send Messages** and **Embed Links** in {channel.mention}.",
            )
        await self.bot.db.set_digest_channel(interaction.guild_id, channel.id)
        await self._say(
            interaction,
            f"✅ Daily due-soon digest will post in {channel.mention}.",
        )

    @app_commands.command(
        name="log", description="Recent activity on lists in this server (admins)."
    )
    @app_commands.default_permissions(manage_guild=True)
    async def log_cmd(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.manage_guild:
            return await self._say(
                interaction, "❌ You need the **Manage Server** permission."
            )
        rows = await self.bot.db.recent_activity(interaction.guild_id, 20)
        if not rows:
            return await self._say(interaction, "No activity recorded yet.")
        lines = []
        for r in rows:
            when = f"<t:{r['created_at']}:R>"
            lines.append(
                f"{when} <@{r['actor_id']}> **{r['action']}** {r['detail'] or ''}".strip()
            )
        e = discord.Embed(
            title=f"{CROWN} Activity log",
            colour=GOLD,
            description="\n".join(lines)[:4000],
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="help", description="How Task Lists works.")
    async def help(self, interaction: discord.Interaction) -> None:
        e = discord.Embed(
            title=f"{CROWN} Task Lists",
            colour=GOLD,
            description=(
                "**Task lists** are static checklists. Timers are optional.\n"
                "**/tasklist create** — draft a list (wizard)\n"
                "**/tasklist import** — paste tasks one-per-line\n"
                "**/tasklist view** — see lists and progress\n"
                "**/tasklist publish** — live public progress embed that auto-updates\n"
                "**/tasklist complete** · **complete-bulk** · **reopen**\n"
                "**/tasklist add-task** · **edit-task** · **remove-task**\n"
                "**/tasklist edit-list** · **archive** · **unarchive** · **delete-list**\n"
                "**/tasklist timer** · **clear-timer** · **clear-all-timers**\n\n"
                "**Reminders** (standalone, with optional recurrence):\n"
                "**/reminder create** · **list** · **edit** · **cancel** · **digest**\n\n"
                "**/tasklist timezone** — zone for typed dates\n"
                "**/tasklist setchannel** · **setdigest** — (admins) defaults\n"
                "**/tasklist log** — (admins) activity trail\n\n"
                f"**Limits:** {MAX_TASKS} tasks per list · 10 user pings · 5 role pings\n"
                "**When:** `MM/DD/YYYY` (9 AM), `MM/DD/YYYY HH:MM`, or `HH:MM` timer.\n"
                "**Repeat:** `daily`, `weekly`, `every 2 days`, `12h`, or blank = once.\n"
                "Times display in *your* local time automatically."
            ),
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


# ============================================================================
# EXTENSION ENTRY POINT (Bot Forge Studio loads this file; reminders + delivery are support cogs)
# ============================================================================
import os as _os  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from bsd_db import Database as _Database  # noqa: E402


async def setup(bot: commands.Bot) -> None:
    if getattr(bot, "db", None) is None:
        bot.db = _Database(_Path(_os.getenv("DATA_DIR", "data")) / "tasks.sqlite3")
        await bot.db.connect()
    await bot.add_cog(TaskLists(bot))
    import bsd_remind
    import bsd_delivery
    await bot.add_cog(bsd_remind.Remind(bot))
    await bot.add_cog(bsd_delivery.Delivery(bot))


async def teardown(bot: commands.Bot) -> None:
    for name in ("Delivery", "Remind"):
        await bot.remove_cog(name)
    bot.tree.remove_command("reminder")      # belt and braces: the group must be gone before a reload re-adds it
    db, bot.db = getattr(bot, "db", None), None
    if db is not None:
        await db.close()

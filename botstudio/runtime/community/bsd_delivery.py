"""Background loop that delivers due reminders and optional daily digests."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from bsd_common import GOLD, log
from bsd_timeutil import format_interval, when_text


class Delivery(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._last_digest_day: dict[int, str] = {}  # guild_id → YYYY-MM-DD

    async def cog_load(self) -> None:
        self.check_due.start()
        self.daily_digest.start()

    async def cog_unload(self) -> None:
        self.check_due.cancel()
        self.daily_digest.cancel()

    @tasks.loop(seconds=15)
    async def check_due(self) -> None:
        try:
            now = int(time.time())
            for r in await self.bot.db.due_reminders(now, 50):
                try:
                    await self._deliver(r, now)
                except discord.HTTPException as e:
                    if e.status >= 500:
                        log.warning(
                            "Discord %s while delivering reminder %s; will retry",
                            e.status,
                            r["id"],
                        )
                        continue
                    log.warning("Could not deliver reminder %s: %s", r["id"], e)
                except Exception:
                    log.exception("Failed delivering reminder %s", r["id"])
                # Always advance state so we don't hammer a broken channel forever
                await self.bot.db.mark_sent_or_reschedule(r["id"], now)
        except Exception:
            log.exception("Reminder sweep failed")

    @check_due.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def daily_digest(self) -> None:
        """Post a due-soon summary once per calendar day per guild that has a digest channel."""
        try:
            now = int(time.time())
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # We don't have a list of all guilds with settings; query via due_soon is per-guild.
            # Iterate guilds the bot is in.
            for guild in self.bot.guilds:
                ch_id = await self.bot.db.get_digest_channel(guild.id)
                if not ch_id:
                    continue
                if self._last_digest_day.get(guild.id) == today:
                    continue
                rows = await self.bot.db.due_soon(guild.id, 48 * 3600, now)
                if not rows:
                    self._last_digest_day[guild.id] = today
                    continue
                channel = guild.get_channel(ch_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(ch_id)
                    except (discord.NotFound, discord.Forbidden):
                        continue
                lines = []
                for r in rows[:20]:
                    label = r.get("message") or (
                        f"#{r['task_position']} {r['task_name']}"
                        if r.get("task_id")
                        else r.get("list_name") or f"#{r['id']}"
                    )
                    rep = format_interval(r.get("interval_seconds"))
                    lines.append(f"• {when_text(r['due_at'])} — {label}" + (f" ({rep})" if rep != "one-shot" else ""))
                e = discord.Embed(
                    title="📋 Due in the next 48 hours",
                    colour=GOLD,
                    description="\n".join(lines)[:4000],
                )
                try:
                    await channel.send(embed=e)
                    self._last_digest_day[guild.id] = today
                except (discord.Forbidden, discord.HTTPException) as err:
                    log.warning("Digest post failed for guild %s: %s", guild.id, err)
        except Exception:
            log.exception("Daily digest failed")

    @daily_digest.before_loop
    async def _wait_digest(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def build_embed(r: dict, now: int) -> discord.Embed:
        due = r["due_at"]
        interval = r.get("interval_seconds")
        if r["task_id"]:
            title = "⏰ Task timer"
            desc = r["message"] or f"Time's up for task **#{r['task_position']} {r['task_name']}**."
        elif r["list_id"]:
            title = "⏳ List countdown"
            desc = r["message"] or (
                f"The countdown for **{r['list_name']}** has ended — "
                f"{r['list_done']}/{r['list_total']} tasks complete."
            )
        else:
            title, desc = "⏰ Reminder", r["message"]
        if interval:
            title = f"🔁 {title}"
            desc = (desc or "") + f"\n*Repeats {format_interval(interval)}*"
        e = discord.Embed(title=title, description=desc or None, colour=GOLD)
        if r["task_id"]:
            e.add_field(name="Task", value=f"#{r['task_position']} · {r['task_name']}")
        if r["list_id"]:
            e.add_field(
                name="List",
                value=f"{r['list_name']} — {r['list_done']}/{r['list_total']} done",
            )
        e.add_field(name="Due", value=f"<t:{due}:F>")
        if now - due > 300:
            e.set_footer(text="Delivered late — the bot was briefly offline.")
        return e

    async def _deliver(self, r: dict, now: int) -> None:
        notify_users = json.loads(r.get("notify_ids") or "[]")
        notify_roles = json.loads(r.get("notify_roles") or "[]")
        ping_everyone = bool(r.get("ping_everyone"))
        embed = self.build_embed(r, now)

        content_parts = []
        if ping_everyone:
            content_parts.append("@everyone")
        content_parts.extend(f"<@&{i}>" for i in notify_roles)
        content_parts.extend(f"<@{i}>" for i in notify_users)
        content = " ".join(content_parts) or None

        allowed = discord.AllowedMentions(
            everyone=ping_everyone,
            roles=[discord.Object(id=i) for i in notify_roles],
            users=[discord.Object(id=i) for i in notify_users],
        )

        channel = None
        if r["channel_id"]:
            channel = self.bot.get_channel(r["channel_id"])
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(r["channel_id"])
                except (discord.NotFound, discord.Forbidden):
                    channel = None

        if channel is not None:
            try:
                await channel.send(content, embed=embed, allowed_mentions=allowed)
                return
            except (discord.Forbidden, discord.NotFound):
                log.info("Can't post in channel %s; falling back to DM", r["channel_id"])

        # Fallback: DM the owner (no role/everyone pings in DMs)
        owner = self.bot.get_user(r["owner_id"]) or await self.bot.fetch_user(r["owner_id"])
        dm_content = " ".join(f"<@{i}>" for i in notify_users) or None
        await owner.send(dm_content, embed=embed)

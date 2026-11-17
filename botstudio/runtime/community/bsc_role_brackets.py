"""Level-bracketing: track roles for levels 10–200 and post member lists into a
bracket channel (forum preferred, so each list becomes a thread).

Admins create the Discord roles themselves, then register them with the bot
under a **game profile** (e.g. Aion2). When a member receives a higher bracket
role within the same game, lower ones for that game are automatically removed
so a user only ever sits in one tracked bracket per game at a time.

Game profiles keep brackets and player UIDs isolated when a server runs more
than one game.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bsp_database import BRACKET_LEVELS, MAX_UID_LEN
from bsp_utils import game_autocomplete, resolve_thread_id


def _level_choices() -> list[app_commands.Choice[int]]:
    return [app_commands.Choice(name=f"Level {lv}", value=lv) for lv in BRACKET_LEVELS]


def _dest_mention(guild: discord.Guild, channel_id: int) -> str:
    """Mention for a stored destination: channel, forum, or a thread in one."""
    chan = guild.get_channel_or_thread(channel_id)
    return chan.mention if chan else f"`{channel_id}` (not found)"


def _thread_prefill(guild: discord.Guild, channel_id: int | None) -> str | None:
    """Only pre-fill the thread-ID box when the current destination really is a
    thread (pre-filling a forum/channel ID would fail validation on submit)."""
    if channel_id is None:
        return None
    return str(channel_id) if isinstance(guild.get_channel_or_thread(channel_id), discord.Thread) else None


async def _resolve_game(db, game_name: str):
    """Return (game_row, None) or (None, error_message)."""
    game = await db.get_game_profile_by_name(game_name)
    if game is None:
        profiles = await db.list_game_profiles()
        if not profiles:
            return None, (
                "No game profiles exist yet. A mod/admin must create one first with "
                "`/game add name:YourGame`."
            )
        names = ", ".join(f"**{p['name']}**" for p in profiles)
        return None, f"No game profile named **{game_name}**. Existing profiles: {names}"
    return game, None


class BracketChannelModal(discord.ui.Modal):
    """Per-level destination, same pattern as the news side's MappingModal.

    The slash command's native picker handles text channels / forums; this
    modal's thread-ID field handles one specific existing forum thread (which
    Discord's picker can't list). A pasted thread ID overrides the picked channel.

    Three uses, depending on what the command hands in:
      * role given            -> /bracket setrole: save role + linked destination
      * level, no role        -> /bracket setchannel level:N: change that level's destination
      * neither               -> /bracket setchannel: set the server-wide default
    """

    def __init__(self, db, level=None, destination=None, role=None,
                 current_id=None, thread_prefill=None, game_id=None, game_name=None):
        if level is not None and game_name:
            title = f"{game_name} L{level} destination"
        elif level is not None:
            title = f"Level {level} destination"
        else:
            title = "Default bracket destination"
        super().__init__(title=title[:45])
        self.db = db
        self.level = level
        self.destination = destination  # TextChannel | ForumChannel | None
        self.role = role                # discord.Role | None
        self.current_id = current_id
        self.game_id = game_id
        self.game_name = game_name

        self.thread_id_input = discord.ui.TextInput(
            label="Thread ID (overrides picked channel)",
            required=False,
            placeholder="Paste a forum thread's ID to post this list in that exact thread",
            default=thread_prefill,
        )
        self.add_item(self.thread_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        dest_id = label = kind = None

        raw = self.thread_id_input.value.strip()
        if raw:
            thread, err = await resolve_thread_id(interaction, raw)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            dest_id, label, kind = thread.id, thread.mention, "thread"
        elif self.destination is not None:
            dest_id, label = self.destination.id, self.destination.mention
            kind = "forum" if isinstance(self.destination, discord.ForumChannel) else "text channel"
        elif self.current_id is not None:
            dest_id, label, kind = self.current_id, _dest_mention(guild, self.current_id), "unchanged"

        if self.role is not None:  # /bracket setrole
            await self.db.set_bracket_role(self.level, self.role.id, self.game_id, dest_id)
            msg = (
                f"**{self.game_name}** Level **{self.level}** bracket is now tracked as {self.role.mention}.\n"
                f"When a member receives this role, any lower **{self.game_name}** bracket roles "
                f"will be removed automatically.\n"
            )
            if dest_id is not None:
                msg += f"`/bracket list game:{self.game_name} level:{self.level}` posts to {label} ({kind})."
            else:
                msg += (
                    f"No destination linked yet — run `/bracket setchannel game:{self.game_name} "
                    f"level:{self.level}` (or set a default with `/bracket setchannel`)."
                )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        if dest_id is None:
            await interaction.response.send_message(
                "Pick a channel/forum on the command, or paste a thread ID in this modal.",
                ephemeral=True,
            )
            return

        if self.level is not None:  # /bracket setchannel level:N
            if not await self.db.set_bracket_level_channel(self.level, self.game_id, dest_id):
                await interaction.response.send_message(
                    f"No role registered for **{self.game_name}** Level {self.level} yet. "
                    f"Use `/bracket setrole` first.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"**{self.game_name}** Level **{self.level}** lists now post to {label} ({kind}).",
                ephemeral=True,
            )
            return

        await self.db.set_bracket_channel(dest_id)  # server-wide default
        await interaction.response.send_message(
            f"Default bracket destination set to {label} ({kind}). Levels without their own "
            f"destination post here.",
            ephemeral=True,
        )


NO_PINGS = discord.AllowedMentions.none()


def _esc(text: str) -> str:
    """Make a display/role name safe to print: no markdown surprises, no @everyone/@here."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


async def _fetch_destination(bot, guild: discord.Guild, dest_id: int):
    """Resolve a stored destination (channel, forum, or thread) -> (obj, error)."""
    dest = guild.get_channel_or_thread(dest_id)
    if dest is None:
        try:
            dest = await bot.fetch_channel(dest_id)
        except discord.HTTPException as e:
            return None, f"Couldn't access the destination: {e}"
    return dest, None


class BracketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------ setup

    group = app_commands.Group(
        name="bracket",
        description="Level-bracket role tracking and member lists (scoped by game profile)",
    )

    @group.command(
        name="setchannel",
        description="Set where a level's member list is posted (forum, channel, or a specific thread)",
    )
    @app_commands.describe(
        game="Game profile this level belongs to (required when setting a level destination)",
        level="Bracket level to set the destination for. Leave blank to set the server-wide default.",
        channel="Forum (new thread per list) or text channel. Leave blank to paste a thread ID instead.",
    )
    @app_commands.choices(level=_level_choices())
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setchannel(
        self,
        interaction: discord.Interaction,
        game: str | None = None,
        level: int | None = None,
        channel: discord.TextChannel | discord.ForumChannel | None = None,
    ):
        db = await self.bot.db_manager.get(interaction.guild_id)
        game_id = game_name = None
        if level is not None:
            if not game:
                await interaction.response.send_message(
                    "When setting a level destination you must specify the **game** profile "
                    "(e.g. `game:Aion2`). Use `/game list` to see profiles.",
                    ephemeral=True,
                )
                return
            g, err = await _resolve_game(db, game)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            game_id, game_name = g["id"], g["name"]
            row = await db.get_bracket_role(level, game_id)
            if row is None:
                await interaction.response.send_message(
                    f"No role registered for **{game_name}** Level {level} yet. "
                    f"Use `/bracket setrole` first.",
                    ephemeral=True,
                )
                return
            current = row["channel_id"]
        else:
            current = await db.get_bracket_channel()
        await interaction.response.send_modal(
            BracketChannelModal(
                db, level=level, destination=channel, current_id=current,
                thread_prefill=_thread_prefill(interaction.guild, current) if channel is None else None,
                game_id=game_id, game_name=game_name,
            )
        )

    @group.command(
        name="setrole",
        description="Register a Discord role as the tracked role for a level bracket under a game",
    )
    @app_commands.describe(
        game="Game profile this bracket belongs to (create one with /game add)",
        level="Bracket level (10, 20, … 200)",
        role="The role members of this bracket should have",
        channel="Forum/text channel this level's lists post to (or paste a thread ID in the next step)",
    )
    @app_commands.choices(level=_level_choices())
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setrole(
        self,
        interaction: discord.Interaction,
        game: str,
        level: int,
        role: discord.Role,
        channel: discord.TextChannel | discord.ForumChannel | None = None,
    ):
        if level not in BRACKET_LEVELS:
            await interaction.response.send_message(
                f"Level must be one of: {', '.join(str(l) for l in BRACKET_LEVELS)}",
                ephemeral=True,
            )
            return

        db = await self.bot.db_manager.get(interaction.guild_id)
        g, err = await _resolve_game(db, game)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        game_id, game_name = g["id"], g["name"]

        # Prevent the same role from being registered under two levels/games.
        existing = await db.list_bracket_roles()
        for row in existing:
            if row["role_id"] == role.id and not (row["level"] == level and row["game_id"] == game_id):
                await interaction.response.send_message(
                    f"{role.mention} is already registered as **{row['game_name']}** "
                    f"Level **{row['level']}**. Remove it from that slot first if you want to reassign it.",
                    ephemeral=True,
                )
                return

        existing_row = await db.get_bracket_role(level, game_id)
        current = existing_row["channel_id"] if existing_row else None
        await interaction.response.send_modal(
            BracketChannelModal(
                db, level=level, destination=channel, role=role, current_id=current,
                thread_prefill=_thread_prefill(interaction.guild, current) if channel is None else None,
                game_id=game_id, game_name=game_name,
            )
        )

    @group.command(
        name="removerole",
        description="Stop tracking a level bracket role for a game",
    )
    @app_commands.describe(
        game="Game profile the bracket belongs to",
        level="Bracket level to un-register",
    )
    @app_commands.choices(level=_level_choices())
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removerole(self, interaction: discord.Interaction, game: str, level: int):
        db = await self.bot.db_manager.get(interaction.guild_id)
        g, err = await _resolve_game(db, game)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        removed = await db.remove_bracket_role(level, g["id"])
        if not removed:
            await interaction.response.send_message(
                f"No role was registered for **{g['name']}** Level {level}.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"**{g['name']}** Level **{level}** is no longer tracked.", ephemeral=True
        )

    @group.command(
        name="roles",
        description="List all tracked bracket roles (optionally filtered by game)",
    )
    @app_commands.describe(game="Optional: only show brackets for this game profile")
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roles(self, interaction: discord.Interaction, game: str | None = None):
        db = await self.bot.db_manager.get(interaction.guild_id)
        game_id = None
        if game:
            g, err = await _resolve_game(db, game)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            game_id = g["id"]

        rows = await db.list_bracket_roles(game_id)
        if not rows:
            hint = f" for **{game}**" if game else ""
            await interaction.response.send_message(
                f"No bracket roles registered{hint} yet. Use `/bracket setrole` to add some "
                f"(create a game profile first with `/game add` if needed).",
                ephemeral=True,
            )
            return

        lines = []
        for r in rows:
            role = interaction.guild.get_role(r["role_id"])
            mention = role.mention if role else f"`{r['role_id']}` (role deleted?)"
            member_count = len(role.members) if role else "?"
            dest_id = r["channel_id"]
            dest = _dest_mention(interaction.guild, dest_id) if dest_id else "default"
            lines.append(
                f"**{r['game_name']}** Level **{r['level']}** → {mention} · "
                f"{member_count} member(s) · posts to {dest}"
            )

        bracket_ch_id = await db.get_bracket_channel()
        footer = "Use `/bracket list` to post a member list."
        if bracket_ch_id:
            ch = interaction.guild.get_channel_or_thread(bracket_ch_id)
            footer = f"Default destination: {ch.name if ch else bracket_ch_id}. " + footer

        embed = discord.Embed(
            title="Tracked Bracket Roles",
            description="\n".join(lines)[:4000],
            color=discord.Color.gold(),
        )
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ list

    @group.command(
        name="list",
        description="Post a list of everyone currently in a bracket role (with game UIDs)",
    )
    @app_commands.describe(
        game="Game profile this bracket belongs to",
        level="Which bracket level to list",
        dry_run="Only show the list to you (don't post to the level's channel)",
    )
    @app_commands.choices(level=_level_choices())
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_bracket(
        self,
        interaction: discord.Interaction,
        game: str,
        level: int,
        dry_run: bool = False,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        db = await self.bot.db_manager.get(interaction.guild_id)
        g, err = await _resolve_game(db, game)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        game_id, game_name = g["id"], g["name"]

        row = await db.get_bracket_role(level, game_id)
        if not row:
            await interaction.followup.send(
                f"No role registered for **{game_name}** Level {level}. Use `/bracket setrole` first.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(row["role_id"])
        if role is None:
            await interaction.followup.send(
                f"The role registered for **{game_name}** Level {level} (`{row['role_id']}`) no longer "
                f"exists in this server. Re-register it with `/bracket setrole`.",
                ephemeral=True,
            )
            return

        members = sorted(role.members, key=lambda m: (m.display_name.lower(), m.id))
        if not members:
            await interaction.followup.send(
                f"{role.mention} has no members currently (or the bot can't see them — "
                f"make sure the **Server Members Intent** is enabled in the Discord "
                f"Developer Portal).",
                ephemeral=True,
            )
            return

        uid_map = await db.get_uids_for_users([m.id for m in members], game_id)

        plural = "s" if len(members) != 1 else ""
        role_name = _esc(role.name)
        header = (
            f"**{game_name}** — **Level {level}** bracket — {role_name}\n"
            f"**{len(members)} player{plural}:**"
        )
        cont_header = f"**{game_name}** — **Level {level}** bracket — {role_name} (continued)"

        lines_out = []
        for i, m in enumerate(members, 1):
            uid = uid_map.get(m.id)
            if uid:
                lines_out.append(f"{i}. {_esc(m.display_name)} — UID: `{_esc(uid)}`")
            else:
                lines_out.append(f"{i}. {_esc(m.display_name)}")
        body = "\n".join(lines_out)
        chunks = _chunk_list(header, body, cont_header)

        if dry_run:
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=NO_PINGS)
            return

        dest_id = await db.get_bracket_destination(level, game_id)
        if dest_id is None:
            await interaction.followup.send(
                f"No destination for **{game_name}** Level {level}. Run "
                f"`/bracket setchannel game:{game_name} level:{level}` "
                f"(or set a server-wide default with `/bracket setchannel`).",
                ephemeral=True,
            )
            return

        destination, err = await _fetch_destination(self.bot, interaction.guild, dest_id)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        posted = 0
        target = destination
        error = None
        try:
            if isinstance(destination, discord.ForumChannel):
                created = await destination.create_thread(
                    name=f"{game_name} Level {level} — {len(members)} members"[:100],
                    content=chunks[0],
                    allowed_mentions=NO_PINGS,
                )
                target = created.thread
                posted = 1
                for chunk in chunks[1:]:
                    await target.send(chunk, allowed_mentions=NO_PINGS)
                    posted += 1
            else:
                for chunk in chunks:
                    await destination.send(chunk, allowed_mentions=NO_PINGS)
                    posted += 1
        except discord.Forbidden:
            error = "Missing permissions to post in the destination."
        except discord.HTTPException as e:
            error = f"Discord error while posting the list: {e}"

        if error:
            partial = (
                f" Posted {posted} of {len(chunks)} message(s) to {target.mention} before it stopped — "
                f"the list there is incomplete."
                if posted else ""
            )
            await interaction.followup.send(error + partial, ephemeral=True)
            return

        await interaction.followup.send(
            f"Posted **{game_name}** Level {level} list → {target.mention} "
            f"({len(members)} member{plural}, {len(chunks)} message{'s' if len(chunks) != 1 else ''})",
            ephemeral=True,
        )

    # ------------------------------------------------------------------ promote

    async def _announce_level_up(
        self, guild: discord.Guild, player: discord.Member, level: int, game_name: str, dest_id: int
    ):
        """Post the congrats message at the level's destination.
        Returns (mention_of_where_it_went, None) or (None, error_text)."""
        destination, err = await _fetch_destination(self.bot, guild, dest_id)
        if err:
            return None, err

        text = (
            f"Congrats {player.mention} you leveled up! 🎉 "
            f"You're now **{game_name} Level {level}**."
        )
        pings = discord.AllowedMentions(users=[player])
        try:
            if isinstance(destination, discord.ForumChannel):
                created = await destination.create_thread(
                    name=f"🎉 {player.display_name} reached {game_name} Level {level}"[:100],
                    content=text,
                    allowed_mentions=pings,
                )
                return created.thread.mention, None
            await destination.send(text, allowed_mentions=pings)
            return destination.mention, None
        except discord.Forbidden:
            return None, "the bot can't post in that destination"
        except discord.HTTPException as e:
            return None, f"Discord error: {e}"

    @group.command(
        name="promote",
        description="Promote a player to a bracket level (swaps their old bracket role and announces it)",
    )
    @app_commands.describe(
        game="Game profile this promotion belongs to",
        player="The member to promote",
        level="The bracket level to promote them to",
    )
    @app_commands.choices(level=_level_choices())
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def promote(
        self,
        interaction: discord.Interaction,
        game: str,
        player: discord.Member,
        level: int,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        db = await self.bot.db_manager.get(interaction.guild_id)
        g, err = await _resolve_game(db, game)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        game_id, game_name = g["id"], g["name"]

        rows = await db.list_bracket_roles(game_id)
        if not rows:
            await interaction.followup.send(
                f"No bracket roles registered for **{game_name}** yet. Use `/bracket setrole` first.",
                ephemeral=True,
            )
            return

        level_by_role = {r["role_id"]: r["level"] for r in rows}
        role_by_level = {r["level"]: r["role_id"] for r in rows}

        if level not in role_by_level:
            await interaction.followup.send(
                f"No role registered for **{game_name}** Level {level}. Use `/bracket setrole` first.",
                ephemeral=True,
            )
            return

        new_role = interaction.guild.get_role(role_by_level[level])
        if new_role is None:
            await interaction.followup.send(
                f"The role registered for **{game_name}** Level {level} no longer exists. "
                f"Re-register it with `/bracket setrole`.",
                ephemeral=True,
            )
            return

        held_levels = [level_by_role[r.id] for r in player.roles if r.id in level_by_role]
        old_level = max(held_levels) if held_levels else None

        to_remove = [
            r
            for r in player.roles
            if r.id in level_by_role and level_by_role[r.id] != level
        ]
        already_has = new_role in player.roles

        try:
            if to_remove:
                await player.remove_roles(
                    *to_remove,
                    reason=f"{game_name} bracket promote to Level {level} by {interaction.user}",
                )
            if not already_has:
                await player.add_roles(
                    new_role,
                    reason=f"{game_name} bracket promote to Level {level} by {interaction.user}",
                )
        except discord.Forbidden:
            await interaction.followup.send(
                "Missing permissions to change roles. Make sure the bot has "
                "**Manage Roles** and its role sits above the bracket roles.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Discord error while updating roles: {e}",
                ephemeral=True,
            )
            return

        parts = [
            f"**{_esc(player.display_name)}** → **{game_name}** Level **{level}** "
            f"({_esc(new_role.name)})"
        ]
        if to_remove:
            parts.append(f"Removed: {', '.join(_esc(r.name) for r in to_remove)}")
        if already_has and not to_remove:
            parts.append("Already had this role — nothing changed.")

        if old_level is None or level > old_level:
            dest_id = await db.get_bracket_destination(level, game_id)
            if dest_id is None:
                parts.append(
                    f"No destination set for **{game_name}** Level {level}, so no announcement was posted "
                    f"(`/bracket setchannel game:{game_name} level:{level}`)."
                )
            else:
                where, err = await self._announce_level_up(
                    interaction.guild, player, level, game_name, dest_id
                )
                if where:
                    parts.append(f"Announced in {where}.")
                else:
                    parts.append(f"Role updated, but the announcement failed: {err}.")

        await interaction.followup.send("\n".join(parts), ephemeral=True, allowed_mentions=NO_PINGS)

    # ------------------------------------------------------------------ auto exclusivity (per game)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """If a member gains a tracked bracket role, strip any lower bracket roles *of the same game*."""
        if before.roles == after.roles:
            return
        if after.guild is None:
            return

        added = set(after.roles) - set(before.roles)
        if not added:
            return

        try:
            db = await self.bot.db_manager.get(after.guild.id)
            rows = await db.list_bracket_roles()
        except Exception:
            return

        if not rows:
            return

        # role_id -> (game_id, level)
        meta = {r["role_id"]: (r["game_id"], r["level"]) for r in rows}

        gained = [(meta[r.id][0], meta[r.id][1], r) for r in added if r.id in meta]
        if not gained:
            return

        # Highest newly-added level per game.
        best_by_game: dict[int, int] = {}
        for game_id, level, _ in gained:
            if game_id not in best_by_game or level > best_by_game[game_id]:
                best_by_game[game_id] = level

        to_remove = []
        for r in after.roles:
            if r.id not in meta:
                continue
            g_id, lv = meta[r.id]
            if g_id in best_by_game and lv < best_by_game[g_id]:
                to_remove.append(r)

        if not to_remove:
            return

        try:
            games = ", ".join(
                str(best_by_game[g]) for g in best_by_game
            )
            await after.remove_roles(
                *to_remove,
                reason=f"Bracket exclusivity: member moved up within game (level {games})",
            )
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


def _chunk_list(header: str, body: str, cont_header: str, limit: int = 1900) -> list[str]:
    """Split a list into messages of at most `limit` characters, breaking only
    between lines. The first message gets `header`; every later one gets
    `cont_header` so it's still identifiable on its own."""
    chunks: list[str] = []
    current = header + "\n\n"
    prefix_len = len(current)
    for line in body.split("\n"):
        if len(current) + len(line) + 1 > limit and len(current) > prefix_len:
            chunks.append(current.rstrip())
            current = cont_header + "\n\n"
            prefix_len = len(current)
        current += line + "\n"
    chunks.append(current.rstrip())
    return chunks


from bsp_database import ensure_db, release_db  # noqa: E402


async def setup(bot: commands.Bot):
    ensure_db(bot)
    await bot.add_cog(BracketCog(bot))


async def teardown(bot: commands.Bot):
    await release_db(bot)

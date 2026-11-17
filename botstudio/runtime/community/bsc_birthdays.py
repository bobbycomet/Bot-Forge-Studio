"""Birthdays & Anniversaries — announcements, temporary roles, GIFs and custom messages (Bot Forge Studio community module).

Members set their birthday and community join date (/birthday set, /anniversary set); admins configure channels,
roles, announcement time, timezone and default messages under /birthday-config.  Everything is stored per server in
data/birthdays/<server id>/*.json — no data is shared between servers.
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
import zoneinfo
import json
import os
from datetime import datetime, timedelta
import calendar

GUILD_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "birthdays")

DEFAULT_GUILD_CONFIG = {
    "announcement_channel_id": None,
    "notification_channel_id": None,
    "birthday_role_id": None,
    "custom_message": "Happy Birthday {user}! 🎉 May your day be filled with joy, laughter, and everything that makes you smile! You're so special to us 💖",
    "anniversary_channel_id": None,
    "anniversary_role_id": None,
    "anniversary_message": "Happy Community Anniversary {user}! 🎊 {years} year{s} in the community — thank you for being here! We're so glad you're part of this family 💖",
    "announcement_time": None,
    "timezone": None,
    "last_role_clean": None,
    "last_anniversary_role_clean": None
}

# ====================== LOAD / SAVE HELPERS ======================
def ensure_guild_dir(guild_id: str):
    path = os.path.join(GUILD_DATA_DIR, guild_id)
    os.makedirs(path, exist_ok=True)
    return path

def load_guild_json(guild_id: str, filename: str, default=None):
    path = os.path.join(GUILD_DATA_DIR, guild_id, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Failed to load {path}: {e}. Using default.")
    return default if default is not None else {}

def save_guild_json(guild_id: str, filename: str, data):
    ensure_guild_dir(guild_id)
    path = os.path.join(GUILD_DATA_DIR, guild_id, filename)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Failed to save {path}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

# In-memory caches
guild_configs = {}
guild_birthdays = {}
guild_anniversaries = {}
guild_announced = {}
loaded_guilds = set()

def load_guild_data(guild_id: str):
    guild_id_str = str(guild_id)
    if guild_id_str in loaded_guilds:
        return
    guild_configs[guild_id_str] = load_guild_json(guild_id_str, "config.json", DEFAULT_GUILD_CONFIG.copy())
    guild_birthdays[guild_id_str] = load_guild_json(guild_id_str, "birthdays.json", {})
    guild_anniversaries[guild_id_str] = load_guild_json(guild_id_str, "anniversaries.json", {})
    guild_announced[guild_id_str] = load_guild_json(guild_id_str, "announced.json", {})
    loaded_guilds.add(guild_id_str)

def save_guild_data(guild_id: str):
    guild_id_str = str(guild_id)
    if guild_id_str not in loaded_guilds:
        return
    save_guild_json(guild_id_str, "config.json", guild_configs.get(guild_id_str, {}))
    save_guild_json(guild_id_str, "birthdays.json", guild_birthdays.get(guild_id_str, {}))
    save_guild_json(guild_id_str, "anniversaries.json", guild_anniversaries.get(guild_id_str, {}))
    save_guild_json(guild_id_str, "announced.json", guild_announced.get(guild_id_str, {}))

# ====================== HELPER FUNCTIONS ======================
def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

# ====================== BOT HANDLE (set when the cog loads) ======================
bot = None   # the running commands.Bot; assigned by BirthdaysCog.cog_load


def _load_known_guilds():
    if os.path.exists(GUILD_DATA_DIR):
        for guild_id in os.listdir(GUILD_DATA_DIR):
            if os.path.isdir(os.path.join(GUILD_DATA_DIR, guild_id)):
                load_guild_data(guild_id)


# ====================== DAILY TASK ======================
@tasks.loop(minutes=1)
async def birthday_check():
    for guild_id_str in list(guild_configs.keys()):
        guild_config = guild_configs[guild_id_str]
        if guild_config.get("announcement_time") is None or guild_config.get("timezone") is None:
            continue

        try:
            tz = zoneinfo.ZoneInfo(guild_config["timezone"])
        except zoneinfo.ZoneInfoNotFoundError:
            print(f"Invalid timezone for guild {guild_id_str}")
            continue

        now = datetime.now(tz)
        today_str = now.date().isoformat()

        guild = bot.get_guild(int(guild_id_str))
        if not guild:
            continue

        guild_birthdays_data = guild_birthdays.get(guild_id_str, {})
        guild_anniversaries_data = guild_anniversaries.get(guild_id_str, {})
        guild_announced_data = guild_announced.setdefault(guild_id_str, {})

        cutoff = (now - timedelta(days=60)).date().isoformat()
        old_keys = [k for k in guild_announced_data if k < cutoff]
        if old_keys:
            for k in old_keys:
                del guild_announced_data[k]
            save_guild_json(guild_id_str, "announced.json", guild_announced_data)

        # Birthday section
        if guild_config.get("announcement_channel_id") and guild_config.get("birthday_role_id"):
            channel = guild.get_channel(guild_config["announcement_channel_id"])
            role = guild.get_role(guild_config["birthday_role_id"])
            if channel and role:
                if guild_config.get("last_role_clean") != today_str:
                    today_birth_ids = {int(uid) for uid, b in guild_birthdays_data.items() if b.get("month") == now.month and b.get("day") == now.day}
                    for member in guild.members:
                        if role in member.roles and member.id not in today_birth_ids:
                            try:
                                await member.remove_roles(role, reason="Birthday celebration ended")
                            except discord.Forbidden:
                                print(f"Missing permission to remove birthday role in guild {guild_id_str} for {member}")
                            except discord.HTTPException as e:
                                print(f"HTTP error removing birthday role in guild {guild_id_str} for {member}: {e}")
                    guild_config["last_role_clean"] = today_str
                    save_guild_json(guild_id_str, "config.json", guild_config)

                today_data = guild_announced_data.setdefault(today_str, {})
                bday_announced = today_data.setdefault("birthdays", [])

                for uid_str, bdata in list(guild_birthdays_data.items()):
                    if bdata.get("month") != now.month or bdata.get("day") != now.day:
                        continue
                    uid = int(uid_str)
                    if uid in bday_announced:
                        continue
                    alert_time_str = bdata.get("custom_alert_time") or guild_config["announcement_time"]
                    try:
                        h, m = map(int, alert_time_str.split(":"))
                        if not (0 <= h < 24 and 0 <= m < 60):
                            raise ValueError
                        alert_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    except Exception as e:
                        print(f"Invalid alert time in guild {guild_id_str} for user {uid_str}: {e}")
                        continue
                    if now >= alert_dt:
                        await announce_birthday(guild, channel, role, uid, bdata, now, guild_config)
                        bday_announced.append(uid)
                        save_guild_json(guild_id_str, "announced.json", guild_announced_data)

        # Anniversary section
        if guild_config.get("anniversary_channel_id") and guild_config.get("anniversary_role_id"):
            channel = guild.get_channel(guild_config["anniversary_channel_id"])
            role = guild.get_role(guild_config["anniversary_role_id"])
            if channel and role:
                if guild_config.get("last_anniversary_role_clean") != today_str:
                    today_ann_ids = {int(uid) for uid, a in guild_anniversaries_data.items() if a.get("month") == now.month and a.get("day") == now.day}
                    for member in guild.members:
                        if role in member.roles and member.id not in today_ann_ids:
                            try:
                                await member.remove_roles(role, reason="Anniversary celebration ended")
                            except discord.Forbidden:
                                print(f"Missing permission to remove anniversary role in guild {guild_id_str} for {member}")
                            except discord.HTTPException as e:
                                print(f"HTTP error removing anniversary role in guild {guild_id_str} for {member}: {e}")
                    guild_config["last_anniversary_role_clean"] = today_str
                    save_guild_json(guild_id_str, "config.json", guild_config)

                today_data = guild_announced_data.setdefault(today_str, {})
                ann_announced = today_data.setdefault("anniversaries", [])

                for uid_str, adata in list(guild_anniversaries_data.items()):
                    if adata.get("month") != now.month or adata.get("day") != now.day:
                        continue
                    uid = int(uid_str)
                    if uid in ann_announced:
                        continue
                    alert_time_str = adata.get("custom_alert_time") or guild_config["announcement_time"]
                    try:
                        h, m = map(int, alert_time_str.split(":"))
                        if not (0 <= h < 24 and 0 <= m < 60):
                            raise ValueError
                        alert_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    except Exception as e:
                        print(f"Invalid alert time in guild {guild_id_str} for user {uid_str}: {e}")
                        continue
                    if now >= alert_dt:
                        years = now.year - adata["year"]
                        await announce_anniversary(guild, channel, role, uid, adata, now, years, guild_config)
                        ann_announced.append(uid)
                        save_guild_json(guild_id_str, "announced.json", guild_announced_data)

# ====================== ANNOUNCE HELPERS ======================
@birthday_check.before_loop
async def _birthday_wait_ready():
    await bot.wait_until_ready()


async def announce_birthday(guild, channel, role, uid, bdata, now, guild_config):
    member = guild.get_member(uid)
    if not member:
        return
    try:
        if role not in member.roles:
            await member.add_roles(role, reason="It's their birthday!")
    except discord.Forbidden:
        print(f"Missing permission to add birthday role to {member} in guild {guild.id}")
    except discord.HTTPException as e:
        print(f"Failed to add birthday role to {member} in guild {guild.id}: {e}")

    message_template = bdata.get("custom_message") or guild_config["custom_message"]
    description = message_template.format(user=member.mention)

    embed = discord.Embed(title="🎂 HAPPY BIRTHDAY! 🎉", description=description, color=0xFF1493)
    embed.set_author(name=f"{member.display_name}'s Special Day", icon_url=member.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)

    gif_url = bdata.get("gif_url")
    if gif_url:
        embed.set_image(url=gif_url)

    unix_ts = int(now.timestamp())
    embed.add_field(name="Celebrated at", value=f"<t:{unix_ts}:F> • <t:{unix_ts}:R>", inline=False)
    embed.set_footer(text="Happy birthday from all of us 💖")

    try:
        await channel.send(content=member.mention, embed=embed)
    except discord.Forbidden:
        print(f"Missing send permission in birthday channel {channel.id} in guild {guild.id}")
    except discord.HTTPException as e:
        print(f"Failed to send birthday announcement in guild {guild.id}: {e}")

async def announce_anniversary(guild, channel, role, uid, adata, now, years, guild_config):
    member = guild.get_member(uid)
    if not member:
        return
    try:
        if role not in member.roles:
            await member.add_roles(role, reason="Community anniversary!")
    except discord.Forbidden:
        print(f"Missing permission to add anniversary role to {member} in guild {guild.id}")
    except discord.HTTPException as e:
        print(f"Failed to add anniversary role to {member} in guild {guild.id}: {e}")

    message_template = adata.get("custom_message") or guild_config["anniversary_message"]
    description = message_template.format(
        user=member.mention,
        years=years,
        s="s" if years != 1 else ""
    )

    embed = discord.Embed(title="🎊 COMMUNITY ANNIVERSARY! 🎉", description=description, color=0x7B68EE)
    embed.set_author(name=f"{member.display_name}'s {years} Year{'s' if years != 1 else ''} Anniversary!", icon_url=member.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)

    gif_url = adata.get("gif_url")
    if gif_url:
        embed.set_image(url=gif_url)

    unix_ts = int(now.timestamp())
    embed.add_field(name="Celebrated at", value=f"<t:{unix_ts}:F> • <t:{unix_ts}:R>", inline=False)
    embed.set_footer(text="Celebrating you today 💖")

    try:
        await channel.send(content=member.mention, embed=embed)
    except discord.Forbidden:
        print(f"Missing send permission in anniversary channel {channel.id} in guild {guild.id}")
    except discord.HTTPException as e:
        print(f"Failed to send anniversary announcement in guild {guild.id}: {e}")

# ====================== HELP COMMAND ======================
@app_commands.command(name="birthday-help", description="Show all birthday & anniversary commands and the setup guide")
async def help_command(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)

    embed = discord.Embed(title="Birthdays & Anniversaries — Fully customizable birthday & anniversary celebrations with embeds, temp roles, GIFs, custom times/messages, and zero cross-server data sharing. Self-hosted and private.", color=0xFF1493)
    embed.add_field(
        name="Everyone — Birthdays",
        value=(
            "`/birthday set <MM-DD>` — Set your birthday\n"
            "`/birthday remove` — Remove your birthday\n"
            "`/birthday list` — List all birthdays\n"
            "`/birthday upcoming` — Show upcoming birthdays\n"
            "`/birthday test` — Preview your birthday card"
        ),
        inline=False
    )
    embed.add_field(
        name="Everyone — Anniversaries",
        value=(
            "`/anniversary set <MM-DD-YYYY>` — Set your community join date\n"
            "`/anniversary remove` — Remove your anniversary\n"
            "`/anniversary list` — List all anniversaries\n"
            "`/anniversary upcoming` — Show upcoming anniversaries\n"
            "`/anniversary test` — Preview your anniversary card"
        ),
        inline=False
    )
    embed.add_field(
        name="Admins only",
        value=(
            "`/birthday-config channel` — Birthday announcement channel\n"
            "`/birthday-config anniversary-channel` — Anniversary announcement channel\n"
            "`/birthday-config notification-channel` — Owner notifications.\n"
            "`/birthday-config role` — Temporary birthday role\n"
            "`/birthday-config anniversary-role` — Temporary anniversary role\n"
            "`/birthday-config time <HH:MM>` — Global alert time\n"
            "`/birthday-config timezone <tz>` — Server timezone\n"
            "`/birthday-config message` — Edit default birthday message\n"
            "`/birthday-config anniversary-message` — Edit default anniversary message\n"
            "`/birthday-config show` — View this server's settings and can be seen as the checklist of what needs to be done to make it work. You must have roles, a timezone set, and so on, or else the auto-messages will not send\n"
            "`/birthday edit @user [message] [HH:MM] [gif_url]` — Custom per-user birthday.\n"
            "`/anniversary edit @user [message] [HH:MM] [gif_url]` — Custom per-user anniversary."
        ),
        inline=False
    )
    embed.add_field(
        name="GIF Support",
        value="Add a GIF link with the `gif_url` option in edit commands — it will appear as a large image in the embed. ❌ Share page URL: https://tenor.com/view/happy-birthday-abc123 — will not work. ✅ Direct media URL: https://media.tenor.com/abc123/happy-birthday.gif — will work. Right click in the browse Gifs window, copy image link. Test with /birthday test or /anniversary test to preview instantly..",
        inline=False
    )
    embed.add_field(
        name="Time Format",
        value="The bot uses 24 hour times, so when you set it, you must say 22:00 for 10 PM, for example. However, it will show 10 PM in the birthday card. This was for making sure the date logic works correctly, see timezone settings for more on UNIX.",
        inline=False
    )
    embed.add_field(
        name="Multi-Server",
        value="Every server has its own independent settings, birthdays, and announcements. You do not need to set the Guild ID (server ID). This is automated and saves separate files.",
        inline=False
    )
    embed.add_field(
        name="Config Commands",
        value="Any command that says config, sends the setting to a config file. This is so when you update a single setting, the others in that same command are preserved. Most bots make you do it all over, this bot lets you update as you go, and not affect the other settings. All config commands are ephemeral (only you see replies).",
        inline=False
    )
    embed.add_field(
        name="Editing Birthdays and Anniversary dates",
        value="Any date altered by the user or Admin resets any custom changes that were made back to default settings. This is for safety of data preservation to stay clean.",
        inline=False
    )
    embed.add_field(
        name="Placeholder for Birthdays",
        value="{user} will automatically change to @username of whoever's card is being edited.",
        inline=False
    )
    embed.add_field(
        name="Placeholder for Anniversaries",
        value="{user} will automatically change to @username of whoever's card is being edited. {years} will calculate years being celebrated. {s} is for if the years ≠ 1, empty if years = 1. {user} has been with us for {years} year{s}! 🎊 Thank you for being part of this community! For 3 years: @username has been with us for 3 years! 🎊 Thank you for being part of this community!  For 1 year: @username has been with us for 1 year! 🎊 Thank you for being part of this community!",
        inline=False
    )
    embed.add_field(
        name="Timezone Setting",
        value="The bot uses IANA (eg, America/Chicago), use https://en.wikipedia.org/wiki/List_of_tz_database_time_zones to find yours. All dates and times will automatically change to other users date and time based on their Discord settings using UNIX. No manual configuration per user.",
        inline=False
    )
    embed.add_field(
        name="Leap Year",
        value="Because February 29th gets skipped, I made sure the bot will celebrate those events on March 1st.",
        inline=False
    )
    embed.add_field(
        name="Setting a Role",
        value="Make a new  birthday and anniversary role if you do not have one already. Use the /birthday-config role or anniversary variant to select the role. The bot does not create the roles, it only assigns them for the user's birthday/anniversary.'",
        inline=False
    )
    embed.add_field(
        name="Questions?",
        value="Ask an admin or Bobby Comet.",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ====================== BIRTHDAY GROUP ======================
birthday_group = app_commands.Group(name="birthday", description="Manage birthdays")

@birthday_group.command(name="set", description="Set or update a birthday")
@app_commands.describe(date="MM-DD or MM/DD format", user="Admin only: set for someone else")
async def birthday_set(interaction: discord.Interaction, date: str, user: discord.Member = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_birthdays_data = guild_birthdays[guild_id]

    target = user or interaction.user
    if target != interaction.user and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You can only set your own birthday.", ephemeral=True)
        return

    date = date.replace("/", "-").strip()
    try:
        m, d = map(int, date.split("-"))
        if not (1 <= m <= 12):
            raise ValueError("Month must be between 01 and 12")
        max_days = calendar.monthrange(2024, m)[1]
        if not (1 <= d <= max_days):
            raise ValueError(f"{calendar.month_name[m]} has at most {max_days} days")
    except ValueError as e:
        await interaction.response.send_message(f"❌ Invalid date: {e}", ephemeral=True)
        return

    uid = str(target.id)
    guild_birthdays_data[uid] = {"month": m, "day": d, "custom_message": None, "custom_alert_time": None, "gif_url": None}
    save_guild_json(guild_id, "birthdays.json", guild_birthdays_data)

    await interaction.response.send_message(
        f"Birthday set for **{target.display_name}**: **{m:02d}-{d:02d}**", ephemeral=True
    )

    if target == interaction.user and not interaction.user.guild_permissions.administrator:
        guild_config = guild_configs[guild_id]
        notify_text = f"📅 **{target.display_name}** ({target.mention}) set their birthday to **{m:02d}-{d:02d}**."
        ch_id = guild_config.get("notification_channel_id")
        notify_ch = interaction.guild.get_channel(ch_id) if ch_id else None
        if notify_ch:
            try:
                await notify_ch.send(notify_text)
            except discord.Forbidden:
                print(f"Missing permission to send in notification channel in guild {guild_id}")
            except discord.HTTPException as e:
                print(f"Failed to send birthday notification in guild {guild_id}: {e}")
        elif interaction.guild.owner:
            try:
                await interaction.guild.owner.send(notify_text)
            except discord.Forbidden:
                print(f"Could not DM guild owner in guild {guild_id} — they may have DMs disabled")
            except discord.HTTPException as e:
                print(f"Failed to DM guild owner in guild {guild_id}: {e}")

@birthday_group.command(name="edit", description="Admin only: set custom message, time, and optional GIF for a user")
@app_commands.describe(
    user="The member",
    message="Custom announcement text (must contain {user})",
    alert_time="Optional custom alert time HH:MM",
    gif_url="Optional direct GIF link"
)
async def birthday_edit(interaction: discord.Interaction, user: discord.Member, message: str = None, alert_time: str = None, gif_url: str = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_birthdays_data = guild_birthdays[guild_id]

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    uid = str(user.id)
    if uid not in guild_birthdays_data:
        await interaction.response.send_message("No birthday recorded for this user.", ephemeral=True)
        return

    updated = False
    if message is not None:
        if "{user}" not in message:
            await interaction.response.send_message("Custom message must include `{user}` placeholder.", ephemeral=True)
            return
        guild_birthdays_data[uid]["custom_message"] = message
        updated = True
    if alert_time is not None:
        try:
            h, m = map(int, alert_time.split(":"))
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError("Time values out of range")
            guild_birthdays_data[uid]["custom_alert_time"] = alert_time
            updated = True
        except ValueError as e:
            await interaction.response.send_message(f"Invalid time format: {e}. Use HH:MM (24-hour).", ephemeral=True)
            return
    if gif_url is not None:
        if ".gif" not in gif_url.lower():
            await interaction.response.send_message("GIF URL should contain '.gif' (query strings are allowed).", ephemeral=True)
            return
        guild_birthdays_data[uid]["gif_url"] = gif_url
        updated = True
    if not updated:
        await interaction.response.send_message("You must provide at least a message or an alert time or a GIF URL.", ephemeral=True)
        return

    save_guild_json(guild_id, "birthdays.json", guild_birthdays_data)
    await interaction.response.send_message(f"Custom settings updated for **{user.display_name}**.", ephemeral=True)

@birthday_group.command(name="remove", description="Remove a birthday entry")
@app_commands.describe(user="Optional: the user (admins only)")
async def birthday_remove(interaction: discord.Interaction, user: discord.Member = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_birthdays_data = guild_birthdays[guild_id]

    target = user or interaction.user
    if target != interaction.user and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You can only remove your own birthday.", ephemeral=True)
        return

    uid = str(target.id)
    if uid in guild_birthdays_data:
        del guild_birthdays_data[uid]
        save_guild_json(guild_id, "birthdays.json", guild_birthdays_data)
        await interaction.response.send_message(f"Birthday removed for **{target.display_name}**.", ephemeral=True)
    else:
        await interaction.response.send_message("No birthday found for that user.", ephemeral=True)

@birthday_group.command(name="list", description="List all recorded birthdays")
async def birthday_list(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_birthdays_data = guild_birthdays[guild_id]

    if not guild_birthdays_data:
        await interaction.response.send_message("No birthdays have been set yet.")
        return

    lines = ["**Recorded Birthdays:**"]
    for uid_str, data in guild_birthdays_data.items():
        member = bot.get_user(int(uid_str))
        name = member.name if member else f"ID {uid_str}"
        lines.append(f"• {name} — {data['month']:02d}-{data['day']:02d}")
    await interaction.response.send_message("\n".join(lines))

@birthday_group.command(name="upcoming", description="Show upcoming birthdays")
async def birthday_upcoming(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]
    guild_birthdays_data = guild_birthdays[guild_id]

    if guild_config.get("timezone") is None:
        await interaction.response.send_message(
            "Timezone has not been configured yet. Ask an admin to run `/birthday-config timezone`.",
            ephemeral=True
        )
        return

    if not guild_birthdays_data:
        await interaction.response.send_message("No birthdays recorded.")
        return

    tz = zoneinfo.ZoneInfo(guild_config["timezone"])
    today = datetime.now(tz).date()
    upcoming = []

    for uid_str, b in guild_birthdays_data.items():
        member = bot.get_user(int(uid_str))
        name = member.name if member else uid_str
        for year_offset in [0, 1]:
            target_year = today.year + year_offset
            try:
                if b["month"] == 2 and b["day"] == 29 and not is_leap_year(target_year):
                    bdate = datetime(target_year, 3, 1).date()
                else:
                    bdate = datetime(target_year, b["month"], b["day"]).date()
                if bdate >= today:
                    upcoming.append(((bdate - today).days, name, b["month"], b["day"]))
                    break
            except ValueError:
                continue

    upcoming.sort()
    if not upcoming:
        await interaction.response.send_message("No upcoming birthdays found.")
        return

    lines = ["**Upcoming Birthdays:**"]
    for days, name, m, d in upcoming[:10]:
        when = "Today! 🎉" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        lines.append(f"• **{name}** — {when} ({m:02d}-{d:02d})")
    await interaction.response.send_message("\n".join(lines))

@birthday_group.command(name="test", description="Preview how your birthday announcement would look")
@app_commands.describe(user="Optional: preview for someone else (admin only)")
async def birthday_test(interaction: discord.Interaction, user: discord.Member = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]

    if guild_config.get("timezone") is None:
        await interaction.response.send_message(
            "Timezone has not been configured yet. Ask an admin to run `/birthday-config timezone`.",
            ephemeral=True
        )
        return

    target = user or interaction.user
    if user and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only admins can preview for others.", ephemeral=True)
        return

    bdata = guild_birthdays[guild_id].get(str(target.id), {})
    message_template = bdata.get("custom_message") or guild_config["custom_message"]
    description = message_template.format(user=target.mention)

    now = datetime.now(zoneinfo.ZoneInfo(guild_config["timezone"]))
    unix = int(now.timestamp())

    embed = discord.Embed(title="🎂 HAPPY BIRTHDAY! 🎉", description=description, color=0xFF1493)
    embed.set_author(name=f"{target.display_name}'s Special Day", icon_url=target.display_avatar.url)
    embed.set_thumbnail(url=target.display_avatar.url)

    gif_url = bdata.get("gif_url")
    if gif_url:
        embed.set_image(url=gif_url)

    embed.add_field(name="Celebrated at", value=f"<t:{unix}:F> • <t:{unix}:R>", inline=False)
    embed.set_footer(text="This is a preview of the announcement")

    await interaction.response.send_message(content=target.mention, embed=embed)

# ====================== ANNIVERSARY GROUP ======================
anniversary_group = app_commands.Group(name="anniversary", description="Manage community anniversaries")

@anniversary_group.command(name="set", description="Set your community join date")
@app_commands.describe(date="MM-DD-YYYY or MM/DD/YYYY format", user="Admin only: set for someone else")
async def anniversary_set(interaction: discord.Interaction, date: str, user: discord.Member = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]
    guild_anniversaries_data = guild_anniversaries[guild_id]

    if guild_config.get("timezone") is None:
        await interaction.response.send_message(
            "Timezone has not been configured yet. Ask an admin to run `/birthday-config timezone`.",
            ephemeral=True
        )
        return

    target = user or interaction.user
    if target != interaction.user and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You can only set your own anniversary.", ephemeral=True)
        return

    date = date.replace("/", "-").strip()
    try:
        parts = date.split("-")
        if len(parts) != 3:
            raise ValueError("Use MM-DD-YYYY format")
        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
        if not (1 <= m <= 12):
            raise ValueError("Month must be between 01 and 12")
        max_days = calendar.monthrange(y, m)[1]
        if not (1 <= d <= max_days):
            raise ValueError(f"{calendar.month_name[m]} {y} has at most {max_days} days")
        tz = zoneinfo.ZoneInfo(guild_config["timezone"])
        now = datetime.now(tz)
        join_date = datetime(y, m, d).date()
        if join_date > now.date():
            raise ValueError("Join date cannot be in the future")
        if y < 2000:
            raise ValueError("Year seems too far in the past — double check!")
    except ValueError as e:
        await interaction.response.send_message(f"❌ Invalid date: {e}", ephemeral=True)
        return

    uid = str(target.id)
    guild_anniversaries_data[uid] = {"month": m, "day": d, "year": y, "custom_message": None, "custom_alert_time": None, "gif_url": None}
    save_guild_json(guild_id, "anniversaries.json", guild_anniversaries_data)

    years_so_far = datetime.now(zoneinfo.ZoneInfo(guild_config["timezone"])).year - y
    await interaction.response.send_message(
        f"Anniversary set for **{target.display_name}**: **{m:02d}-{d:02d}-{y}** "
        f"({years_so_far} year{'s' if years_so_far != 1 else ''} in the community!)",
        ephemeral=True
    )

@anniversary_group.command(name="edit", description="Admin only: set custom message, time, and optional GIF for a user")
@app_commands.describe(
    user="The member",
    message="Custom announcement text (must contain {user})",
    alert_time="Optional custom alert time HH:MM",
    gif_url="Optional direct GIF link"
)
async def anniversary_edit(interaction: discord.Interaction, user: discord.Member, message: str = None, alert_time: str = None, gif_url: str = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_anniversaries_data = guild_anniversaries[guild_id]

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    uid = str(user.id)
    if uid not in guild_anniversaries_data:
        await interaction.response.send_message("No anniversary recorded for this user.", ephemeral=True)
        return

    updated = False
    if message is not None:
        if "{user}" not in message:
            await interaction.response.send_message("Custom message must include `{user}` placeholder.", ephemeral=True)
            return
        guild_anniversaries_data[uid]["custom_message"] = message
        updated = True
    if alert_time is not None:
        try:
            h, m = map(int, alert_time.split(":"))
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError("Time values out of range")
            guild_anniversaries_data[uid]["custom_alert_time"] = alert_time
            updated = True
        except ValueError as e:
            await interaction.response.send_message(f"Invalid time format: {e}. Use HH:MM (24-hour).", ephemeral=True)
            return
    if gif_url is not None:
        if ".gif" not in gif_url.lower():
            await interaction.response.send_message("GIF URL should contain '.gif' (query strings are allowed).", ephemeral=True)
            return
        guild_anniversaries_data[uid]["gif_url"] = gif_url
        updated = True
    if not updated:
        await interaction.response.send_message("You must provide at least a message or an alert time or a GIF URL.", ephemeral=True)
        return

    save_guild_json(guild_id, "anniversaries.json", guild_anniversaries_data)
    await interaction.response.send_message(f"Custom settings updated for **{user.display_name}**.", ephemeral=True)

@anniversary_group.command(name="remove", description="Remove an anniversary entry")
@app_commands.describe(user="Optional: the user (admins only)")
async def anniversary_remove(interaction: discord.Interaction, user: discord.Member = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_anniversaries_data = guild_anniversaries[guild_id]

    target = user or interaction.user
    if target != interaction.user and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You can only remove your own anniversary.", ephemeral=True)
        return

    uid = str(target.id)
    if uid in guild_anniversaries_data:
        del guild_anniversaries_data[uid]
        save_guild_json(guild_id, "anniversaries.json", guild_anniversaries_data)
        await interaction.response.send_message(f"Anniversary removed for **{target.display_name}**.", ephemeral=True)
    else:
        await interaction.response.send_message("No anniversary found for that user.", ephemeral=True)

@anniversary_group.command(name="list", description="List all recorded anniversaries")
async def anniversary_list(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]
    guild_anniversaries_data = guild_anniversaries[guild_id]

    if guild_config.get("timezone") is None:
        await interaction.response.send_message(
            "Timezone has not been configured yet. Ask an admin to run `/birthday-config timezone`.",
            ephemeral=True
        )
        return

    if not guild_anniversaries_data:
        await interaction.response.send_message("No anniversaries have been set yet.")
        return

    tz = zoneinfo.ZoneInfo(guild_config["timezone"])
    current_year = datetime.now(tz).year
    lines = ["**Recorded Anniversaries:**"]
    for uid_str, data in guild_anniversaries_data.items():
        member = bot.get_user(int(uid_str))
        name = member.name if member else f"ID {uid_str}"
        years = current_year - data["year"]
        lines.append(f"• {name} — {data['month']:02d}-{data['day']:02d}-{data['year']} ({years} yr{'s' if years != 1 else ''})")
    await interaction.response.send_message("\n".join(lines))

@anniversary_group.command(name="upcoming", description="Show upcoming community anniversaries")
async def anniversary_upcoming(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]
    guild_anniversaries_data = guild_anniversaries[guild_id]

    if guild_config.get("timezone") is None:
        await interaction.response.send_message(
            "Timezone has not been configured yet. Ask an admin to run `/birthday-config timezone`.",
            ephemeral=True
        )
        return

    if not guild_anniversaries_data:
        await interaction.response.send_message("No anniversaries recorded.")
        return

    tz = zoneinfo.ZoneInfo(guild_config["timezone"])
    today = datetime.now(tz).date()
    upcoming = []

    for uid_str, a in guild_anniversaries_data.items():
        member = bot.get_user(int(uid_str))
        name = member.name if member else uid_str
        for year_offset in [0, 1]:
            target_year = today.year + year_offset
            try:
                if a["month"] == 2 and a["day"] == 29 and not is_leap_year(target_year):
                    adate = datetime(target_year, 3, 1).date()
                else:
                    adate = datetime(target_year, a["month"], a["day"]).date()
                if adate >= today:
                    years_celebrating = target_year - a["year"]
                    upcoming.append(((adate - today).days, name, a["month"], a["day"], years_celebrating))
                    break
            except ValueError:
                continue

    upcoming.sort()
    if not upcoming:
        await interaction.response.send_message("No upcoming anniversaries found.")
        return

    lines = ["**Upcoming Anniversaries:**"]
    for days, name, m, d, years in upcoming[:10]:
        when = "Today! 🎊" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        lines.append(f"• **{name}** — {when} ({m:02d}-{d:02d}, {years} yr{'s' if years != 1 else ''})")
    await interaction.response.send_message("\n".join(lines))

@anniversary_group.command(name="test", description="Preview how your anniversary announcement would look")
@app_commands.describe(user="Optional: preview for someone else (admin only)")
async def anniversary_test(interaction: discord.Interaction, user: discord.Member = None):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]

    if guild_config.get("timezone") is None:
        await interaction.response.send_message(
            "Timezone has not been configured yet. Ask an admin to run `/birthday-config timezone`.",
            ephemeral=True
        )
        return

    target = user or interaction.user
    if user and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only admins can preview for others.", ephemeral=True)
        return

    uid_str = str(target.id)
    if uid_str not in guild_anniversaries[guild_id]:
        await interaction.response.send_message("No anniversary set. Use `/anniversary set MM-DD-YYYY` first.", ephemeral=True)
        return

    adata = guild_anniversaries[guild_id][uid_str]
    now = datetime.now(zoneinfo.ZoneInfo(guild_config["timezone"]))
    years = now.year - adata["year"]
    message_template = adata.get("custom_message") or guild_config["anniversary_message"]
    description = message_template.format(user=target.mention, years=years, s="s" if years != 1 else "")

    unix = int(now.timestamp())
    embed = discord.Embed(title="🎊 COMMUNITY ANNIVERSARY! 🎉", description=description, color=0x7B68EE)
    embed.set_author(name=f"{target.display_name}'s {years} Year{'s' if years != 1 else ''} Anniversary!", icon_url=target.display_avatar.url)
    embed.set_thumbnail(url=target.display_avatar.url)

    gif_url = adata.get("gif_url")
    if gif_url:
        embed.set_image(url=gif_url)

    embed.add_field(name="Joined", value=f"{adata['month']:02d}-{adata['day']:02d}-{adata['year']}", inline=True)
    embed.add_field(name="Years in Community", value=str(years), inline=True)
    embed.add_field(name="Celebrated at", value=f"<t:{unix}:F> • <t:{unix}:R>", inline=False)
    embed.set_footer(text="This is a preview of the announcement")

    await interaction.response.send_message(content=target.mention, embed=embed)

# ====================== CONFIG GROUP ======================
config_group = app_commands.Group(name="birthday-config", description="Configure birthdays & anniversaries (admin only)")

@config_group.command(name="show", description="Display current configuration for this server")
@app_commands.checks.has_permissions(administrator=True)
async def config_show(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_config = guild_configs[guild_id]

    embed = discord.Embed(title="Birthday Configuration — This Server", color=0xFF1493)

    def status(val):
        return f"✅ {val}" if val else "❌ Not set"

    embed.add_field(name="Announcement Time", value=status(guild_config.get("announcement_time")), inline=True)
    embed.add_field(name="Timezone", value=status(guild_config.get("timezone")), inline=True)
    embed.add_field(name="Birthday Channel", value=f"<#{guild_config.get('announcement_channel_id')}>" if guild_config.get("announcement_channel_id") else "Not set", inline=False)
    embed.add_field(name="Birthday Role", value=f"<@&{guild_config.get('birthday_role_id')}>" if guild_config.get("birthday_role_id") else "Not set", inline=False)
    embed.add_field(name="Anniversary Channel", value=f"<#{guild_config.get('anniversary_channel_id')}>" if guild_config.get("anniversary_channel_id") else "Not set", inline=False)
    embed.add_field(name="Anniversary Role", value=f"<@&{guild_config.get('anniversary_role_id')}>" if guild_config.get("anniversary_role_id") else "Not set", inline=False)
    embed.add_field(name="Notification Channel", value=f"<#{guild_config.get('notification_channel_id')}>" if guild_config.get("notification_channel_id") else "Not set", inline=False)
    embed.add_field(name="Default Birthday Message", value=guild_config["custom_message"][:120] + "..." if len(guild_config["custom_message"]) > 120 else guild_config["custom_message"], inline=False)
    embed.add_field(name="Default Anniversary Message", value=guild_config["anniversary_message"][:120] + "..." if len(guild_config["anniversary_message"]) > 120 else guild_config["anniversary_message"], inline=False)
    embed.set_footer(text="Announcements will not be sent until time and timezone are set.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@config_group.command(name="channel", description="Set the birthday announcement channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel for birthday announcements")
async def config_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_configs[guild_id]["announcement_channel_id"] = channel.id
    save_guild_json(guild_id, "config.json", guild_configs[guild_id])
    await interaction.response.send_message(f"Birthday announcements will go to {channel.mention}", ephemeral=True)

@config_group.command(name="anniversary-channel", description="Set the anniversary announcement channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel for anniversary announcements")
async def config_anniversary_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_configs[guild_id]["anniversary_channel_id"] = channel.id
    save_guild_json(guild_id, "config.json", guild_configs[guild_id])
    await interaction.response.send_message(f"Anniversary announcements will go to {channel.mention}", ephemeral=True)

@config_group.command(name="notification-channel", description="Set channel for birthday-set notifications to owner")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel")
async def config_notification_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_configs[guild_id]["notification_channel_id"] = channel.id
    save_guild_json(guild_id, "config.json", guild_configs[guild_id])
    await interaction.response.send_message(f"Birthday set notifications will go to {channel.mention}", ephemeral=True)

@config_group.command(name="role", description="Set the temporary birthday role")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="Role given on a member's birthday")
async def config_role(interaction: discord.Interaction, role: discord.Role):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_configs[guild_id]["birthday_role_id"] = role.id
    save_guild_json(guild_id, "config.json", guild_configs[guild_id])
    await interaction.response.send_message(f"Birthday role set to **{role.name}**", ephemeral=True)

@config_group.command(name="anniversary-role", description="Set the temporary anniversary role")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="Role given on a member's community anniversary")
async def config_anniversary_role(interaction: discord.Interaction, role: discord.Role):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    guild_configs[guild_id]["anniversary_role_id"] = role.id
    save_guild_json(guild_id, "config.json", guild_configs[guild_id])
    await interaction.response.send_message(f"Anniversary role set to **{role.name}**", ephemeral=True)

@config_group.command(name="time", description="Set global announcement time (24-hour HH:MM)")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(time="HH:MM")
async def config_time(interaction: discord.Interaction, time: str):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    try:
        h, m = map(int, time.split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
        guild_configs[guild_id]["announcement_time"] = time
        save_guild_json(guild_id, "config.json", guild_configs[guild_id])
        await interaction.response.send_message(f"Global announcement time set to **{time}**", ephemeral=True)
    except ValueError as e:
        await interaction.response.send_message(f"Invalid time: {e}. Please use 24-hour format: `HH:MM`", ephemeral=True)

@config_group.command(name="timezone", description="Set the server's timezone")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(timezone="e.g. America/New_York, UTC, Europe/London")
async def config_timezone(interaction: discord.Interaction, timezone: str):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)
    try:
        zoneinfo.ZoneInfo(timezone)
        guild_configs[guild_id]["timezone"] = timezone
        save_guild_json(guild_id, "config.json", guild_configs[guild_id])
        await interaction.response.send_message(f"Timezone set to **{timezone}**", ephemeral=True)
    except zoneinfo.ZoneInfoNotFoundError:
        await interaction.response.send_message("Invalid timezone name. Example: `America/New_York`", ephemeral=True)

@config_group.command(name="message", description="Edit the default birthday announcement message")
@app_commands.checks.has_permissions(administrator=True)
async def config_message(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)

    class MessageModal(discord.ui.Modal, title="Edit Default Birthday Message"):
        message = discord.ui.TextInput(
            label="Announcement text",
            style=discord.TextStyle.paragraph,
            default=guild_configs[guild_id]["custom_message"],
            max_length=1900,
            placeholder="Use {user} for the mention",
            required=True
        )
        async def on_submit(self, inter: discord.Interaction):
            guild_configs[guild_id]["custom_message"] = self.message.value
            save_guild_json(guild_id, "config.json", guild_configs[guild_id])
            await inter.response.send_message("Default birthday message updated.", ephemeral=True)

    await interaction.response.send_modal(MessageModal())

@config_group.command(name="anniversary-message", description="Edit the default anniversary announcement message")
@app_commands.checks.has_permissions(administrator=True)
async def config_anniversary_message(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    load_guild_data(guild_id)

    class AnniversaryMessageModal(discord.ui.Modal, title="Edit Default Anniversary Message"):
        message = discord.ui.TextInput(
            label="Announcement text",
            style=discord.TextStyle.paragraph,
            default=guild_configs[guild_id]["anniversary_message"],
            max_length=1900,
            placeholder="Use {user}, {years}, and {s} (plural suffix)",
            required=True
        )
        async def on_submit(self, inter: discord.Interaction):
            guild_configs[guild_id]["anniversary_message"] = self.message.value
            save_guild_json(guild_id, "config.json", guild_configs[guild_id])
            await inter.response.send_message("Default anniversary message updated.", ephemeral=True)

    await interaction.response.send_modal(AnniversaryMessageModal())

# ====================== COG ENTRY POINT (Bot Forge Studio loads this file as an extension) ======================
class BirthdaysCog(commands.Cog):
    def __init__(self, b: commands.Bot):
        self.b = b
        self._names: list[str] = []

    async def cog_load(self):
        global bot
        bot = self.b
        _load_known_guilds()
        for cmd in (birthday_group, anniversary_group, config_group, help_command):
            self.b.tree.add_command(cmd)
            self._names.append(cmd.name)
        if not birthday_check.is_running():
            birthday_check.start()

    async def cog_unload(self):
        birthday_check.cancel()
        for n in self._names:
            self.b.tree.remove_command(n)


async def setup(b: commands.Bot):
    await b.add_cog(BirthdaysCog(b))

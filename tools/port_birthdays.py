import re
src = open('/mnt/user-data/uploads/chatkakke.py', encoding='utf-8').read()
def sub(old, new, count=1):
    global src
    assert old in src, old[:70]
    src = src.replace(old, new, count)

# --- header: drop the hard-coded token, the global bot, on_ready and the script entry point
a = src.index('import discord'); b = src.index('GUILD_DATA_DIR')
src = '''"""Birthdays & Anniversaries — announcements, temporary roles, GIFs and custom messages (Bot Studio community module).

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

''' + src[b:]
src = re.sub(r'^TOKEN = .*\n', '', src, flags=re.M)
sub('GUILD_DATA_DIR = "guild_data"', 'GUILD_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "birthdays")')
a = src.index('# ====================== BOT SETUP ======================')
b = src.index('# ====================== DAILY TASK ======================')
src = src[:a] + '''# ====================== BOT HANDLE (set when the cog loads) ======================
bot = None   # the running commands.Bot; assigned by BirthdaysCog.cog_load


def _load_known_guilds():
    if os.path.exists(GUILD_DATA_DIR):
        for guild_id in os.listdir(GUILD_DATA_DIR):
            if os.path.isdir(os.path.join(GUILD_DATA_DIR, guild_id)):
                load_guild_data(guild_id)


''' + src[b:]
# tree.command help -> group-free command with a safe name
sub('@tree.command(name="help", description="Show all chatkakke commands and setup guide")',
    '@app_commands.command(name="birthday-help", description="Show all birthday & anniversary commands and the setup guide")')
src = src.replace('Show all chatkakke commands', 'Show all birthday commands')
src = src.replace('title="chatkakke — Fully customizable', 'title="Birthdays & Anniversaries — Fully customizable')
src = src.replace('chatkakke wishes you the best day ever', 'Happy birthday from all of us')
src = src.replace('chatkakke celebrates you today', 'Celebrating you today')
src = src.replace('This is a preview — chatkakke style', 'This is a preview of the announcement')
src = src.replace('Configure chatkakke (admin only)', 'Configure birthdays & anniversaries (admin only)')
src = src.replace('chatkakke Configuration — This Server', 'Birthday Configuration — This Server')
src = src.replace('/help', '/birthday-help')
# cog entry point instead of tree registration + bot.run
a = src.index('# ====================== REGISTER ALL COMMANDS ======================')
src = src[:a] + '''# ====================== COG ENTRY POINT (Bot Studio loads this file as an extension) ======================
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
'''
open('botstudio/runtime/community/bsc_birthdays.py', 'w', encoding='utf-8').write(src)

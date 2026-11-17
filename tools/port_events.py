import re
src = open('/mnt/user-data/uploads/mysticalgoonwizard.py', encoding='utf-8').read()
def sub(old, new, count=1):
    global src
    assert old in src, old[:70]
    src = src.replace(old, new, count)

# header comment block -> docstring
a = src.index('import os')
src = '''"""Event Scheduler — spawn / drop / countdown reminders, a quest board, a trade board and guild events with signups
(Bot Studio community module).

Every "add" command takes one argument — the target channel, picked from an autocomplete dropdown — and opens a form
(modal) for the details.  Edit commands open a pre-filled form; remove commands ask for confirmation.

  Spawn reminders   /addspawn /editspawn /removespawn /listspawns /nextspawn
  Item drops        /addrop /editdrop /removedrop /listdrops /nextdrop
  Countdowns        /countdown /editcountdown /removecountdown /listcountdowns /nextcountdown
  Quest board       /questpost /listquests
  Trade board       /tradepost /listtrades
  Guild events      /eventcreate /listevents      (sign-up buttons, roster, cancel)
  Utilities         /upcoming /listtargets /events-help

Discord modals hold at most 5 fields, so related values share a field (e.g. "14:30 America/New_York").
Times are scheduled with APScheduler in each entry's own timezone; data lives in data/events.json.
"""
''' + src[a:]
sub('''LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mystical-goon")''', 'log = logging.getLogger("bot.events")')
sub('DATA_FILE = os.getenv("DATA_FILE", "spawns.json")', 'DATA_FILE = os.path.join(os.getenv("DATA_DIR", "data"), "events.json")\nos.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)')
sub('''INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.guild_messages = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)
''', '''bot = None   # the running commands.Bot; assigned by EventsCog.cog_load

_COMMANDS: list = []        # slash commands collected at import time, registered by the cog


def _cmd(**kw):
    """Stand-in for @bot.tree.command: build the command now, add it to the tree when the cog loads."""
    def deco(fn):
        c = app_commands.command(**kw)(fn)
        _COMMANDS.append(c)
        return c
    return deco
''')
src = src.replace('@bot.tree.command(', '@_cmd(')
src = src.replace('name="help", description="🧙‍♂️ How to use the Mystical Goon Wizard"', 'name="events-help", description="How to use the event scheduler"')
src = src.replace('Mystical Goon Wizard', 'Event Scheduler').replace('mystical-goon', 'bot.events')
# on_ready block -> removed (the cog does it)
a = src.index('@bot.event\nasync def on_ready():')
b = src.index('# ═══════════════════════════════════════════════════════════════\n# SLASH COMMANDS')
src = src[:a] + src[b:]
a = src.index('# ═══════════════════════════════════════════════════════════════\n# Run')
src = src[:a] + '''# ═══════════════════════════════════════════════════════════════
# COG ENTRY POINT (Bot Studio loads this file as an extension)
# ═══════════════════════════════════════════════════════════════
class EventsCog(commands.Cog):
    def __init__(self, b: commands.Bot):
        self.b = b
        self._names: list[tuple[str, discord.AppCommandType]] = []

    async def cog_load(self):
        global bot
        bot = self.b
        load_data()
        schedule_all()
        if not scheduler.running:
            scheduler.start()
        for c in _COMMANDS:
            self.b.tree.add_command(c)
            self._names.append((c.name, discord.AppCommandType.chat_input))

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.b.guilds:
            ensure_guild(str(g.id))
        schedule_all()

    async def cog_unload(self):
        try:
            scheduler.remove_all_jobs()
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        for name, kind in self._names:
            self.b.tree.remove_command(name, type=kind)


async def setup(b: commands.Bot):
    await b.add_cog(EventsCog(b))
'''
open('botstudio/runtime/community/bsc_events.py', 'w', encoding='utf-8').write(src)

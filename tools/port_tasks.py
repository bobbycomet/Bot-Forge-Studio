import re
U = '/mnt/user-data/uploads/'
OUT = 'botstudio/runtime/community/'
def rd(n): return open(U + n, encoding='utf-8').read()

def common(s):
    s = re.sub(r'^from common import', 'from bsd_common import', s, flags=re.M)
    s = re.sub(r'^from db import', 'from bsd_db import', s, flags=re.M)
    s = re.sub(r'^from timeutil import', 'from bsd_timeutil import', s, flags=re.M)
    s = re.sub(r'^from ui import', 'from bsd_ui import', s, flags=re.M)
    s = s.replace('Royal Decrees', 'Task Lists').replace('/decree', '/tasklist').replace('group_name="decree"', 'group_name="tasklist"')
    s = s.replace('"Decrees"', '"TaskLists"').replace('class Decrees(', 'class TaskLists(').replace('Decrees(bot)', 'TaskLists(bot)')
    s = s.replace('"""/tasklist commands', '"""/tasklist commands')
    s = s.replace('The royal scribes dropped their quill — something went wrong.', 'Something went wrong.')
    s = s.replace("Delivered late — the realm was briefly without its herald.", "Delivered late — the bot was briefly offline.")
    s = s.replace('getLogger("royal_decrees")', 'getLogger("bot.tasks")').replace('CROWN = "👑"', 'CROWN = "📋"')
    return s

db = rd('db.py')
db = db.replace('self._conn = await aiosqlite.connect(', 'pending = aiosqlite.connect(') if 'self._conn = await aiosqlite.connect(' in db else db
open(OUT + 'bsd_db.py', 'w', encoding='utf-8').write(db)
for src, dst in (('common.py', 'bsd_common.py'), ('timeutil.py', 'bsd_timeutil.py'), ('ui.py', 'bsd_ui.py'),
                 ('remind.py', 'bsd_remind.py'), ('delivery.py', 'bsd_delivery.py')):
    s = common(rd(src))
    i = s.rfind('async def setup(')
    if i > 0:
        s = s[:i].rstrip() + '\n'
    open(OUT + dst, 'w', encoding='utf-8').write(s)
main = common(rd('decrees.py'))
i = main.rindex('async def setup(')
main = main[:i].rstrip() + '''


# ============================================================================
# EXTENSION ENTRY POINT (Bot Studio loads this file; reminders + delivery are support cogs)
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
'''
main = main.replace('"""/tasklist commands: static task lists you can complete, with optional timers."""',
                    '"""Task Lists & Reminders — /tasklist (static task lists you can complete, with optional timers and a live progress embed) and\n/reminder (one-off and recurring reminders posted in a channel).  Timers and reminders are delivered by a background loop;\nusers set their timezone once with /tasklist timezone.  (Bot Studio community module.)"""')
open(OUT + 'bsc_task_lists.py', 'w', encoding='utf-8').write(main)

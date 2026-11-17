import re
U = '/mnt/user-data/uploads/'
OUT = 'botstudio/runtime/community/'

def rd(n): return open(U + n, encoding='utf-8').read()

def common(s):
    s = s.replace('from utils import', 'from bsp_utils import').replace('from database import', 'from bsp_database import')
    s = s.replace('newsrouter', 'router').replace('News Router', 'Post Router').replace('news routing bot', 'post router')
    s = s.replace('news_router', 'post_router').replace('NewsRouter', 'PostRouter')
    s = s.replace('Group(name="uid"', 'Group(name="gameuid"').replace('`/uid ', '`/gameuid ').replace('/uid set', '/gameuid set')
    s = re.sub(r'`/uid(\b)', r'`/gameuid\1', s)
    s = s.replace('        name="uid",', '        name="gameuid",')
    s = s.replace('@app_commands.command(\n        name="test",', '@app_commands.command(\n        name="router-test",')
    s = s.replace('`/test`', '`/router-test`')
    # strip the original setup()
    i = s.rindex('async def setup(')
    return s[:i].rstrip() + '\n\n\n'

def strip_main(s):
    return s

# ---------------------------------------------------------------- shared support files
db = rd('database.py')
db = db.replace('self._conn = await aiosqlite.connect(self.path)',
                'pending = aiosqlite.connect(self.path)\n        pending.daemon = True        # never keep the process alive after the bot stopped\n        self._conn = await pending')
db = db.replace('news router', 'post router').replace('News Router', 'Post Router').replace('News router', 'Post router')
db += '''


# ---------------------------------------------------------------- Bot Studio glue
def ensure_db(bot) -> "DatabaseManager":
    """One DatabaseManager per bot, shared by every router / bracket / game-profile module."""
    if getattr(bot, "db_manager", None) is None:
        bot.db_manager = DatabaseManager(os.path.join(os.getenv("DATA_DIR", "data"), "router"))
    return bot.db_manager


async def release_db(bot) -> None:
    mgr = getattr(bot, "db_manager", None)
    if mgr is not None:
        await mgr.close_all()          # reopens lazily on next use, so other modules keep working
'''
open(OUT + 'bsp_database.py', 'w', encoding='utf-8').write(db)
ut = rd('utils.py').replace('news router', 'post router')
open(OUT + 'bsp_utils.py', 'w', encoding='utf-8').write(ut)

HDR = '"""%s"""\n'

# ---------------------------------------------------------------- Post Router (setup + router)
setup = common(rd('setup_cog.py'))
setup = '''"""Post Router — watch a hub channel and route matching posts to channels, forums or threads (Bot Studio community module).

Admins create mappings (/router addmapping): author IDs, source servers/channels, keywords, and a destination.
Posts that land in the hub (channel follows, forwards, webhooks) are matched by specificity — source beats keyword
beats author — and copied or forwarded to every mapping at the winning tier.  /router scan shows the IDs that
actually appear in the hub; /router-test dry-runs a mapping against the newest matching post.
"""
''' + setup + '''from bsp_database import ensure_db, release_db  # noqa: E402


async def setup(bot: commands.Bot):
    ensure_db(bot)
    await bot.add_cog(SetupCog(bot))
    import bsp_router
    await bot.add_cog(bsp_router.RouterCog(bot))


async def teardown(bot: commands.Bot):
    await bot.remove_cog("RouterCog")       # defined in a support module, so the extension does not own it
    await release_db(bot)
'''
open(OUT + 'bsc_post_router.py', 'w', encoding='utf-8').write(setup)
open(OUT + 'bsp_router.py', 'w', encoding='utf-8').write(common(rd('router_cog.py')))

# ---------------------------------------------------------------- Role Brackets
br = common(rd('bracket_cog.py'))
br += '''from bsp_database import ensure_db, release_db  # noqa: E402


async def setup(bot: commands.Bot):
    ensure_db(bot)
    await bot.add_cog(BracketCog(bot))


async def teardown(bot: commands.Bot):
    await release_db(bot)
'''
open(OUT + 'bsc_role_brackets.py', 'w', encoding='utf-8').write(br)

# ---------------------------------------------------------------- Game Profiles & UIDs
g = common(rd('game_cog.py'))
g += '''from bsp_database import ensure_db, release_db  # noqa: E402


async def setup(bot: commands.Bot):
    ensure_db(bot)
    await bot.add_cog(GameCog(bot))
    import bsp_uid
    await bot.add_cog(bsp_uid.UidCog(bot))


async def teardown(bot: commands.Bot):
    await bot.remove_cog("UidCog")
    await release_db(bot)
'''
open(OUT + 'bsc_game_profiles.py', 'w', encoding='utf-8').write(g)
open(OUT + 'bsp_uid.py', 'w', encoding='utf-8').write(common(rd('uid_cog.py')))

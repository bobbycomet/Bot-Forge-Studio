import asyncio, sys, os, tempfile, importlib, json, shutil
sys.path.insert(0, '..')
from botstudio.core import examples, project
import discord
from discord.ext import commands

async def run(name, fn):
    g = fn(); res = project.build(g)
    assert res.ok, res.errors
    d = tempfile.mkdtemp()
    for p, t in res.files.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
        open(os.path.join(d, p), 'w').write(t)
    sys.path.insert(0, d)
    for m in [m for m in sys.modules if m == 'bs_runtime' or m.startswith('cogs')]:
        del sys.modules[m]
    import bs_runtime as bs
    intents = discord.Intents.default(); intents.message_content = True; intents.members = True
    bot = commands.Bot(command_prefix='!', intents=intents)
    ctl = await bs.setup_control(bot)
    cmds = [c.qualified_name for c in bot.tree.get_commands()]
    print(f"{name:40s} cogs={len(bot.cogs)} errors={ctl.errors} commands={cmds}")
    ctl.watch.cancel()
    sys.path.remove(d)
    # hot reload simulation
    if bot.cogs:
        path = next(iter(ctl.loaded))
        ok = await ctl._load(path, "x", reload=True)
        assert ok, ctl.errors
    await bot.close()

async def main():
    for name, fn in examples.EXAMPLES.items():
        await run(name, fn)
asyncio.run(main())

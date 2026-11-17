import sys, json
sys.path.insert(0, '..')
from botstudio.core import examples, project, services
from botstudio.core.model import DeployTarget
for name, fn in examples.EXAMPLES.items():
    g = fn()
    res = project.build(g)
    print("==", name, "ok" if res.ok else res.errors, res.warnings, sorted(res.intents), [c.slug for c in res.cogs])
g = examples.mod_timeout(); res = project.build(g)
for c in res.cogs: print(c.source)
g = examples.coinflip(); res = project.build(g)
print(res.cogs[0].source[-700:])
print(res.files['bot.py'][:1500])
for init in ("systemd-system","systemd-user","runit","openrc","windows","process"):
    s = services.generate(g.settings, DeployTarget(), "/opt/x", init, "botuser")
    print(init, list(s))

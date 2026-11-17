"""Economy nodes: wallets, spending, transfers, timed rewards, boards and item inventories.

Everything is stored in the shared key/value table (``data/bot.db``), so any number of currencies
("accounts") can coexist — gold, gems, tickets, points — and the web dashboard can show them.
"""
from __future__ import annotations

from .codegen import GenError
from .model import NodeSpec, Prop, register
from .nodes import D, EX

ACCOUNT_HELP = "Name of the wallet table. Use different names for different currencies (coins, gems, tickets…)."


def _acct(ctx) -> str:
    a = str(ctx.prop("account") or "wallet").strip()
    return repr(a if a else "wallet")


def _guild(ctx) -> str:
    return ctx.scope.get("guild", "None")


def _user(ctx, key="user") -> str:
    u = ctx.val(key, ctx.default_user() or "None")
    if u == "None":
        raise GenError(f"{ctx.node.spec.title} [{ctx.node.id}]: no member — wire one into '{key}' or use it under a Slash Command / On Message trigger")
    return u


def _acc_prop():
    return Prop("account", "Currency / wallet name", "str", "wallet", help=ACCOUNT_HELP)


def _amount(label="Amount", default=100):
    return Prop("amount", label, "float", default, port=True)


def gen_balance(ctx):
    ctx.emit(f"{ctx.var('balance')} = await bs.eco_balance({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)})")


register(NodeSpec("eco.balance", "Get Balance", "Economy", "action",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX(), D("balance", "int")],
                  props=[_acc_prop()], gen=gen_balance,
                  description="Reads a member's balance (defaults to the member who triggered the flow)."))


def gen_give(ctx):
    ctx.emit(f"{ctx.var('balance')} = await bs.eco_add({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)}, {ctx.val('amount')})")


register(NodeSpec("eco.give", "Give Currency", "Economy", "action",
                  inputs=[EX("in"), D("user", "user"), D("amount", "int")], outputs=[EX(), D("balance", "int")],
                  props=[_acc_prop(), _amount("Amount (negative takes away)", 100)], gen=gen_give,
                  description="Adds currency to a member's wallet. Use a negative amount to remove it (may go below zero)."))


def gen_spend(ctx):
    v = ctx.var
    floor = False
    ctx.emit(f"{v('ok')}, {v('balance')} = await bs.eco_spend({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)}, {ctx.val('amount')}, {floor})")
    ctx.emit(f"if {v('ok')}:")
    with ctx.block("paid"):
        pass
    ctx.emit("else:")
    with ctx.block("insufficient"):
        pass


register(NodeSpec("eco.spend", "Spend Currency", "Economy", "flow",
                  inputs=[EX("in"), D("user", "user"), D("amount", "int")],
                  outputs=[EX("paid"), EX("insufficient", "not enough"), D("balance", "int")],
                  props=[_acc_prop(), _amount("Price", 50)], gen=gen_spend,
                  description="Takes currency only if the member can afford it — 'paid' or 'not enough'."))


def gen_transfer(ctx):
    v = ctx.var
    src = _user(ctx, "from_user")
    dst = ctx.val("to_user", "None")
    if dst == "None":
        raise GenError(f"Transfer Currency [{ctx.node.id}]: wire the receiving member into 'to_user'")
    ctx.emit(f"{v('ok')}, {v('balance')} = await bs.eco_transfer({_acct(ctx)}, {_guild(ctx)}, {src}, {dst}, {ctx.val('amount')})")
    ctx.emit(f"if {v('ok')}:")
    with ctx.block("sent"):
        pass
    ctx.emit("else:")
    with ctx.block("failed"):
        pass


register(NodeSpec("eco.transfer", "Transfer Currency", "Economy", "flow",
                  inputs=[EX("in"), D("from_user", "user"), D("to_user", "user"), D("amount", "int")],
                  outputs=[EX("sent"), EX("failed", "failed"), D("balance", "int")],
                  props=[_acc_prop(), _amount("Amount", 10)], gen=gen_transfer,
                  description="Moves currency between two members. Fails on insufficient funds, a zero amount, or paying yourself."))


def gen_claim(ctx):
    v = ctx.var
    secs = f"float({ctx.val('hours')}) * 3600"
    ctx.emit(f"{v('ready')}, {v('seconds')} = await bs.cooldown_claim({str(ctx.prop('name') or 'daily')!r}, {_guild(ctx)}, {_user(ctx)}, {secs})")
    ctx.emit(f"{v('remaining')} = bs.human_duration({v('seconds')})")
    ctx.emit(f"if {v('ready')}:")
    with ctx.block("ready"):
        pass
    ctx.emit("else:")
    with ctx.block("waiting"):
        pass


register(NodeSpec("eco.claim", "Timed Reward (Cooldown)", "Economy", "flow",
                  inputs=[EX("in"), D("user", "user")],
                  outputs=[EX("ready"), EX("waiting"), D("remaining", "str"), D("seconds", "int")],
                  props=[Prop("name", "Cooldown name", "str", "daily", help="Each name has its own timer per member."),
                         Prop("hours", "Hours between claims", "float", 24, port=True)],
                  gen=gen_claim,
                  description="Lets a member through once per period (daily, weekly, hourly work…). 'waiting' gets the time left."))


def gen_top(ctx):
    ctx.emit(f"{ctx.var('board')} = await bs.eco_board({_acct(ctx)}, {_guild(ctx)}, int({ctx.val('limit')}), {str(ctx.prop('symbol') or '')!r})")


register(NodeSpec("eco.top", "Top Balances", "Economy", "action",
                  inputs=[EX("in")], outputs=[EX(), D("board", "str")],
                  props=[_acc_prop(), Prop("limit", "How many", "int", 10, port=True), Prop("symbol", "Currency symbol", "str", "🪙")],
                  gen=gen_top, description="A ready-to-post ranking of the richest members (ties into Reply / Send)."))


def gen_item_give(ctx):
    ctx.emit(f"{ctx.var('count')} = await bs.item_add({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)}, {ctx.val('item')}, int({ctx.val('qty')}))")


_ITEM_PROPS = [Prop("account", "Inventory name", "str", "inventory", help="Separate inventories per game / shop."),
               Prop("item", "Item", "str", "Potion", fmt=True, port=True), Prop("qty", "Quantity", "int", 1, port=True)]

register(NodeSpec("eco.item_give", "Give Item", "Economy", "action",
                  inputs=[EX("in"), D("user", "user"), D("item", "str"), D("qty", "int")], outputs=[EX(), D("count", "int")],
                  props=_ITEM_PROPS, gen=gen_item_give, description="Adds items to a member's inventory."))


def gen_item_take(ctx):
    v = ctx.var
    ctx.emit(f"{v('ok')}, {v('count')} = await bs.item_take({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)}, {ctx.val('item')}, int({ctx.val('qty')}))")
    ctx.emit(f"if {v('ok')}:")
    with ctx.block("taken"):
        pass
    ctx.emit("else:")
    with ctx.block("missing"):
        pass


register(NodeSpec("eco.item_take", "Use / Take Item", "Economy", "flow",
                  inputs=[EX("in"), D("user", "user"), D("item", "str"), D("qty", "int")],
                  outputs=[EX("taken"), EX("missing", "doesn't have it"), D("count", "int")],
                  props=_ITEM_PROPS, gen=gen_item_take, description="Removes items if the member has enough; otherwise 'missing'."))


def gen_item_count(ctx):
    ctx.emit(f"{ctx.var('count')} = await bs.item_count({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)}, {ctx.val('item')})")


register(NodeSpec("eco.item_count", "Count Item", "Economy", "action",
                  inputs=[EX("in"), D("user", "user"), D("item", "str")], outputs=[EX(), D("count", "int")],
                  props=_ITEM_PROPS[:2], gen=gen_item_count, description="How many of an item a member holds."))


def gen_inventory(ctx):
    ctx.emit(f"{ctx.var('text')} = await bs.inventory_text({_acct(ctx)}, {_guild(ctx)}, {_user(ctx)})")


register(NodeSpec("eco.inventory", "Inventory List", "Economy", "action",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX(), D("text", "str")],
                  props=_ITEM_PROPS[:1], gen=gen_inventory, description="A formatted list of everything a member owns."))

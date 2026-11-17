"""Teaching validators for node properties.

A validator is ``fn(value, node, runner) -> None | str``: None means "correct", a string is the message shown under the coach
panel — written to explain *why* the value is rejected, not just that it is.
"""
from __future__ import annotations

import ast
import re


def regex(pattern: str, msg: str):
    rx = re.compile(pattern)

    def check(v, node, run):
        return None if rx.fullmatch(str(v or "")) else msg
    return check


def length(lo: int, hi: int, what: str):
    def check(v, node, run):
        n = len(str(v or "").strip())
        if n < lo:
            return f"{what} must be at least {lo} character{'s' if lo != 1 else ''} — it is empty or too short."
        if n > hi:
            return f"{what} is {n} characters; Discord allows at most {hi}."
        return None
    return check


def contains_all(parts, msg: str):
    def check(v, node, run):
        t = str(v or "")
        missing = [p for p in parts if p not in t]
        return None if not missing else msg.replace("{missing}", ", ".join(missing))
    return check


def contains_any(parts, msg: str):
    def check(v, node, run):
        t = str(v or "")
        return None if any(p in t for p in parts) else msg
    return check


def pipe_items(minimum: int, what: str = "items"):
    def check(v, node, run):
        items = [x.strip() for x in str(v or "").split("|") if x.strip()]
        if len(items) < minimum:
            return f"Separate {what} with the | character — you need at least {minimum} (found {len(items)}), e.g. `Heads|Tails`."
        return None
    return check


def placeholders(known=None, need_one: bool = True):
    """Text with {placeholders}: each must be a plain dotted name; at least one required."""
    rx = re.compile(r"\{([^{}]*)\}")

    def check(v, node, run):
        t = str(v or "")
        if not t.strip():
            return "This text is empty."
        found = rx.findall(t)
        bad = [f for f in found if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9]*)*", f)]
        if bad:
            return (f"`{{{bad[0]}}}` is not a valid placeholder. Placeholders are plain names with optional dots, like {{user.mention}} — "
                    "calls and expressions are not allowed (that is what keeps shared project files safe).")
        if need_one and not found:
            return "Add at least one {placeholder}, for example {user.mention}, so the reply is personal."
        if known:
            unknown = [f for f in found if f.split(".")[0] not in known]
            if unknown:
                return f"`{{{unknown[0]}}}` is not available here. In this flow you can use: " + ", ".join("{%s}" % k for k in known) + "."
        return None
    return check


def form_fields(min_fields: int = 1):
    """'label | id | short/long | required | …' rows for the Show Form node."""
    def check(v, node, run):
        from ...core.nodes_ui import field_rows
        rows = field_rows(v)
        if len(rows) < min_fields:
            return f"Add at least {min_fields} field, one per line, as `label | id | short/long | required | placeholder`."
        raw = [ln for ln in str(v or "").splitlines() if ln.strip() and not ln.strip().startswith(("#", "//"))]
        if len(raw) > 5:
            return "Discord forms hold at most 5 fields — extra lines are ignored. Use a Multi-step Form for more."
        for r in rows:
            if r[2] and r[2].lower()[0] not in "sl":
                return f"Field '{r[0]}': the third column must be `short` or `long`, not `{r[2]}`."
        return None
    return check


def python_code(*, must_assign=(), must_contain=(), forbid=(), need_try: bool = False):
    """Custom Python: must parse, and must follow the lesson's rules. `forbid` is [(substring, why)]."""
    def check(v, node, run):
        src = str(v or "")
        if not src.strip():
            return "The editor is empty. Open it with “Open in editor…” and write the code."
        # the node's code runs inside an async handler: wrap it the same way for the syntax check
        wrapped = "async def _h():\n" + "\n".join("    " + ln for ln in src.splitlines()) + "\n"
        try:
            tree = ast.parse(wrapped)
        except SyntaxError as e:
            return f"Syntax error on line {max(1, (e.lineno or 2) - 1)}: {e.msg}. The Problems list in the editor shows the same message."
        assigned = {t.id for n in ast.walk(tree) for t in (n.targets if isinstance(n, ast.Assign) else [getattr(n, 'target', None)])
                    if isinstance(t, ast.Name)}
        for name in must_assign:
            if name not in assigned:
                return f"Your code must assign `{name}` — that variable becomes the node's `{name}` output port, so nothing downstream would receive a value."
        for s in must_contain:
            if isinstance(s, tuple):
                if s[0] not in src:
                    return s[1]
            elif s not in src:
                return f"The lesson expects `{s}` somewhere in your code."
        if need_try and not any(isinstance(n, ast.Try) for n in ast.walk(tree)):
            return "Wrap the risky part in try / except so one bad input cannot crash the command (see “Error handling” above)."
        for s, why in forbid:
            if s in src:
                return f"`{s}` is not allowed here: {why}"
        return None
    return check


def browser_js(*, must_contain=()):
    def check(v, node, run):
        from ...core import web_validate
        src = str(v or "")
        if not src.strip():
            return "The editor is empty. Open it with “Open in editor…”."
        errs = [i for i in web_validate.check_browser_js(src) if i[2] == "error"]
        if errs:
            return f"Line {errs[0][0] + 1}: {errs[0][3]}"
        for s in must_contain:
            if isinstance(s, tuple):
                if s[0] not in src:
                    return s[1]
            elif s not in src:
                return f"The lesson expects `{s}` in your script."
        return None
    return check


def html_page(*, must_contain=()):
    def check(v, node, run):
        from ...core import web_validate
        src = str(v or "")
        errs = [i for i in web_validate.check_html(src) if i[2] == "error"]
        if errs:
            return f"Line {errs[0][0] + 1}: {errs[0][3]}"
        for s in must_contain:
            if isinstance(s, tuple):
                if s[0] not in src:
                    return s[1]
            elif s not in src:
                return f"The page should contain `{s}`."
        return None
    return check


def css_code(*, must_contain=()):
    def check(v, node, run):
        from ...core import web_validate
        src = str(v or "")
        errs = [i for i in web_validate.check_css(src) if i[2] == "error"]
        if errs:
            return f"Line {errs[0][0] + 1}: {errs[0][3]}"
        for s in must_contain:
            if isinstance(s, tuple):
                if s[0] not in src:
                    return s[1]
            elif s not in src:
                return f"The stylesheet should contain `{s}`."
        return None
    return check


def route_path(v, node, run):
    from ...core.nodes_web import ROUTE_PATH
    if not ROUTE_PATH.match(str(v or "")):
        return "A route path must start with /api/custom and use letters, numbers, - _ . or :params — for example /api/custom/stats."
    return None


def number_between(lo: float, hi: float, what: str):
    def check(v, node, run):
        try:
            n = float(v)
        except (TypeError, ValueError):
            return f"{what} must be a number."
        return None if lo <= n <= hi else f"{what} must be between {lo:g} and {hi:g}."
    return check


def all_of(*checks):
    def check(v, node, run):
        for c in checks:
            r = c(v, node, run)
            if r:
                return r
        return None
    return check

"""Syntax checking for generated and hand-written JavaScript.

Uses ``node --check`` when a Node.js binary is on this machine (exact, same parser the bot
will run on), and falls back to a small bracket/quote balancer when it is not, so the editor
still catches the typical typos without Node installed.

Results are cached by content hash: Studio rebuilds on every edit and most cogs do not change.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from typing import Optional

# (line 0-based, column 0-based, message)
SyntaxIssue = tuple[int, int, str]

_CACHE: "OrderedDict[str, Optional[SyntaxIssue]]" = OrderedDict()
_CACHE_MAX = 256
_NODE: Optional[str] = None
_NODE_PROBED = False


def node_binary() -> Optional[str]:
    """Path to a usable ``node`` on this machine (``BOTSTUDIO_NODE`` overrides the PATH lookup)."""
    global _NODE, _NODE_PROBED
    if not _NODE_PROBED:
        _NODE_PROBED = True
        _NODE = os.environ.get("BOTSTUDIO_NODE") or shutil.which("node")
    return _NODE


def reset_node_probe() -> None:
    global _NODE_PROBED
    _NODE_PROBED = False
    _CACHE.clear()


_LOC = re.compile(r"^(?:.*[\\/])?[^:\n]*:(\d+)\s*$")


def _node_check(code: str) -> Optional[SyntaxIssue]:
    node = node_binary()
    if not node:
        return _balance_check(code)
    with tempfile.TemporaryDirectory(prefix="bs_jscheck_") as d:
        f = os.path.join(d, "chk.js")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(code)
        try:
            p = subprocess.run([node, "--check", f], capture_output=True, timeout=8, text=True,
                               encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return _balance_check(code)
    if p.returncode == 0:
        return None
    lines = p.stderr.splitlines()
    line_no, col, msg = 0, 0, "Syntax error"
    for i, ln in enumerate(lines):
        m = _LOC.match(ln)
        if m and i == 0:
            line_no = max(0, int(m.group(1)) - 1)
        if ln.strip().startswith("^"):
            col = max(0, len(ln) - len(ln.lstrip()))
        if ln.startswith(("SyntaxError:", "ReferenceError:", "Error:")):
            msg = ln.split(":", 1)[1].strip()
            break
    return line_no, col, msg


def _balance_check(code: str) -> Optional[SyntaxIssue]:
    """Very small tokenizer: strings, template literals, comments, and bracket pairing."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int, int]] = []
    i, n, line, col0 = 0, len(code), 0, 0
    tpl_depth: list[int] = []  # brace depth at each open `${`

    def adv(k=1):
        nonlocal i, line, col0
        for _ in range(k):
            if i < n and code[i] == "\n":
                line += 1
                col0 = i + 1
            i += 1

    while i < n:
        c = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            while i < n and code[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            sl, sc = line, i - col0
            end = code.find("*/", i + 2)
            if end < 0:
                return sl, sc, "Unterminated comment"
            adv(end + 2 - i)
            continue
        if c in "'\"":
            q, sl, sc = c, line, i - col0
            adv()
            while i < n and code[i] != q:
                if code[i] == "\\":
                    adv()
                if i < n and code[i] == "\n":
                    return sl, sc, "Unterminated string"
                adv()
            if i >= n:
                return sl, sc, "Unterminated string"
            adv()
            continue
        if c == "`":
            sl, sc = line, i - col0
            adv()
            while i < n and code[i] != "`":
                if code[i] == "\\":
                    adv(2)
                    continue
                if code[i] == "$" and i + 1 < n and code[i + 1] == "{":
                    # skip the interpolation with a nested balance count
                    depth = 1
                    adv(2)
                    while i < n and depth:
                        if code[i] == "{":
                            depth += 1
                        elif code[i] == "}":
                            depth -= 1
                        adv()
                    continue
                adv()
            if i >= n:
                return sl, sc, "Unterminated template literal"
            adv()
            continue
        if c in "([{":
            stack.append((c, line, i - col0))
        elif c in ")]}":
            if not stack or stack[-1][0] != pairs[c]:
                return line, i - col0, f"Unexpected '{c}'"
            stack.pop()
        adv()
    if stack:
        c, l, cc = stack[-1]
        return l, cc, f"'{c}' was never closed"
    return None


def check(code: str) -> Optional[SyntaxIssue]:
    """Syntax-check a complete JavaScript file (CommonJS). None means OK."""
    key = hashlib.sha1(code.encode("utf-8", "replace")).hexdigest()
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    res = _node_check(code)
    _CACHE[key] = res
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return res


WRAP_HEAD = "(async function __bs_custom(interaction, message, member, bot, bs, req, res, reaction, user) {\n"
WRAP_TAIL = "\n});\n"


def check_snippet(source: str) -> Optional[SyntaxIssue]:
    """Check handler-body code (as typed into a Custom JavaScript node). Lines are reported 0-based
    relative to *source*."""
    if not source.strip():
        return None
    res = check(WRAP_HEAD + source + WRAP_TAIL)
    if res is None:
        return None
    line, col, msg = res
    return max(0, line - 1), col, msg

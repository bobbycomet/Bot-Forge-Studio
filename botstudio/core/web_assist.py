"""Editor support for the Web nodes: completions and diagnostics for HTML, CSS and browser JavaScript."""
from __future__ import annotations

import re

from . import web_validate as wv


def _word(source: str, pos: int) -> tuple[str, int]:
    i = pos
    while i > 0 and (source[i - 1].isalnum() or source[i - 1] in "-_.<#:@"):
        i -= 1
    return source[i:pos], i


def completions(runtime: str, source: str, pos: int, Completion) -> list:
    prefix, _ = _word(source, pos)
    p = prefix.lstrip("<").lower()
    out = []
    if runtime == "html":
        for t in wv.HTML_TAGS:
            void = t in wv.VOID
            out.append(Completion(f"<{t}>", f"<{t}></{t}>" if not void else f"<{t}>", "HTML element", "func", "html"))
        for a in wv.HTML_ATTRS:
            out.append(Completion(a, f'{a}=""' if not a.endswith("-") else a, "attribute", "var", "html"))
        out += [Completion("form (post to a route)", '<form id="f"><input name="x" required><button type="submit">Send</button></form>', "snippet", "snip", "html"),
                Completion("table", "<table>\n  <thead><tr><th>Name</th><th>Value</th></tr></thead>\n  <tbody id=\"rows\"></tbody>\n</table>", "snippet", "snip", "html")]
    elif runtime == "css":
        for prop in wv.CSS_PROPS:
            out.append(Completion(prop, f"{prop}: ;", "CSS property", "var", "css"))
        out += [Completion("flex row", "display: flex;\ngap: 12px;\nalign-items: center;", "snippet", "snip", "css"),
                Completion("grid cards", "display: grid;\ngrid-template-columns: repeat(auto-fill, minmax(180px, 1fr));\ngap: 12px;", "snippet", "snip", "css"),
                Completion("@media mobile", "@media (max-width: 600px) {\n  \n}", "snippet", "snip", "css"),
                Completion("@keyframes", "@keyframes fade {\n  from { opacity: 0; }\n  to { opacity: 1; }\n}", "snippet", "snip", "css"),
                Completion("var(--accent)", "var(--accent)", "dashboard theme colour", "var", "css")]
    else:
        for n in wv.BROWSER_NAMES:
            out.append(Completion(n, n, "browser API", "mod", "browserjs"))
        out += [Completion("fetch json", "const data = await BotStudio.api('/api/custom/hello');", "call your Web Route", "snip", "browserjs"),
                Completion("live updates", "BotStudio.live((msg) => {\n  if (msg.type === 'event') console.log(msg.event, msg.data);\n});", "WebSocket", "snip", "browserjs"),
                Completion("form submit", "form.addEventListener('submit', async (e) => {\n  e.preventDefault();\n  \n});", "snippet", "snip", "browserjs"),
                Completion("render list", "list.replaceChildren(...items.map((x) => {\n  const li = document.createElement('li');\n  li.textContent = x;\n  return li;\n}));", "safe rendering (textContent)", "snip", "browserjs")]
    return [c for c in out if not p or c.label.lstrip("<").lower().startswith(p) or p in c.label.lower()][:60]


def diagnostics(runtime: str, source: str, Diagnostic) -> list:
    return [Diagnostic(line, col, msg, sev, "") for line, col, sev, msg in wv.check(runtime, source)]

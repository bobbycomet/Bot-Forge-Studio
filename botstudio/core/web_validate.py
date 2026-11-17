"""Validation for the browser-side Web nodes (HTML, CSS, browser JavaScript).

These run in the visitor's browser under the dashboard's Content-Security-Policy
(``default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'``), so most "mistakes" here are really
policy violations: inline scripts, inline handlers, inline styles, external CDNs.  Each issue is
``(line0, col0, severity, message)`` where severity is "error" | "warning" | "info".
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from . import jscheck

Issue = tuple[int, int, str, str]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}")
EXTERNAL = re.compile(r"^\s*(?:https?:)?//", re.I)

HTML_TAGS = ("a abbr article aside audio b blockquote body br button canvas caption code col colgroup dd details div dl dt em fieldset "
             "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr i img input label legend li main mark meter nav ol optgroup "
             "option output p pre progress q s section select small span strong sub summary sup table tbody td template textarea tfoot "
             "th thead time tr u ul video").split()
HTML_ATTRS = ("id class href src alt title type name value placeholder for action method disabled checked selected required readonly "
              "min max step rows cols colspan rowspan data- aria-label aria-hidden role tabindex hidden lang target rel width height "
              "autocomplete autofocus maxlength minlength pattern multiple").split()
CSS_PROPS = ("align-items align-self animation animation-delay animation-duration background background-color background-image "
             "border border-bottom border-color border-radius border-top bottom box-shadow box-sizing color column-gap cursor display "
             "flex flex-direction flex-grow flex-shrink flex-wrap font font-family font-size font-weight gap grid grid-template-areas "
             "grid-template-columns grid-template-rows height justify-content justify-items left letter-spacing line-height margin "
             "margin-bottom margin-left margin-right margin-top max-height max-width min-height min-width object-fit opacity outline "
             "overflow overflow-x overflow-y padding padding-bottom padding-left padding-right padding-top place-items pointer-events "
             "position right row-gap text-align text-decoration text-overflow text-transform top transform transition user-select "
             "vertical-align visibility white-space width word-break z-index").split()
BROWSER_NAMES = ("document window console fetch WebSocket localStorage sessionStorage location history navigator setTimeout setInterval "
                 "clearTimeout clearInterval requestAnimationFrame addEventListener querySelector querySelectorAll getElementById "
                 "createElement textContent classList dataset JSON Promise URL URLSearchParams FormData AbortController BotStudio").split()


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.issues: list[Issue] = []
        self.ids: dict[str, int] = {}

    def _pos(self):
        line, col = self.getpos()
        return max(0, line - 1), col

    def handle_starttag(self, tag, attrs):
        line, col = self._pos()
        a = dict((k, v or "") for k, v in attrs)
        if tag in ("html", "head", "body"):
            self.issues.append((line, col, "info", f"<{tag}> is added for you — write only what goes inside the page body."))
        if tag == "script":
            if "src" not in a:
                self.issues.append((line, col, "warning", "Inline <script> is blocked by the Content-Security-Policy. Put the code in a Browser JavaScript node and connect it to this page."))
            elif EXTERNAL.match(a["src"]):
                self.issues.append((line, col, "warning", "External scripts (CDNs) are blocked by the policy. Bundle the code in a Browser JavaScript node."))
        if tag == "link" and a.get("rel", "").lower() == "stylesheet" and EXTERNAL.match(a.get("href", "")):
            self.issues.append((line, col, "warning", "External stylesheets are blocked by the policy. Use a CSS node."))
        if tag == "style":
            self.issues.append((line, col, "warning", "Inline <style> is blocked. Use a CSS node connected to this page."))
        for k, v in a.items():
            if k.startswith("on"):
                self.issues.append((line, col, "warning", f"Inline handler `{k}=` is blocked. Use element.addEventListener in the Browser JavaScript node."))
            if k == "style":
                self.issues.append((line, col, "warning", "Inline style= attributes are blocked. Give the element a class and style it in the CSS node."))
        if tag == "img":
            if "alt" not in a:
                self.issues.append((line, col, "info", "Images should have an alt attribute (accessibility)."))
            if EXTERNAL.match(a.get("src", "")) and "cdn.discordapp.com" not in a.get("src", ""):
                self.issues.append((line, col, "warning", "External images are blocked except cdn.discordapp.com and data: URLs."))
        if tag == "form" and EXTERNAL.match(a.get("action", "")):
            self.issues.append((line, col, "error", "Forms may only submit to this site (form-action 'self')."))
        if "id" in a:
            if a["id"] in self.ids:
                self.issues.append((line, col, "error", f"Duplicate id '{a['id']}' (first used on line {self.ids[a['id']] + 1}). ids must be unique."))
            self.ids[a["id"]] = line
        if tag not in VOID:
            self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        line, col = self._pos()
        if tag in VOID:
            return
        if not self.stack or all(t != tag for t, _ in self.stack):
            self.issues.append((line, col, "error", f"</{tag}> has no matching opening tag."))
            return
        while self.stack and self.stack[-1][0] != tag:
            t, l = self.stack.pop()
            self.issues.append((l, 0, "error", f"<{t}> is never closed (closed implicitly by </{tag}> on line {line + 1})."))
        self.stack.pop()


def check_html(code: str) -> list[Issue]:
    p = _Html()
    try:
        p.feed(code or "")
        p.close()
    except Exception as e:   # html.parser is lenient; this is a safety net
        return [(0, 0, "error", f"Could not parse: {e}")]
    for tag, line in p.stack:
        p.issues.append((line, 0, "error", f"<{tag}> is never closed."))
    if TOKEN_RE.search(code or ""):
        p.issues.append((0, 0, "error", "This looks like a Discord bot token. Never put credentials in frontend code."))
    return sorted(p.issues, key=lambda i: (i[0], i[1]))


def _strip_css_comments(code: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), code, flags=re.S)


def check_css(code: str) -> list[Issue]:
    issues: list[Issue] = []
    text = _strip_css_comments(code or "")
    depth, stack = 0, []
    for ln, line in enumerate(text.splitlines()):
        for col, ch in enumerate(line):
            if ch == "{":
                depth += 1
                stack.append((ln, col))
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    issues.append((ln, col, "error", "Unexpected '}' — there is no matching '{'."))
                    depth = 0
        s = line.strip()
        if re.search(r"@import\s+(?:url\()?\s*['\"]?(?:https?:)?//", s, re.I) or re.search(r"url\(\s*['\"]?(?:https?:)?//", s, re.I):
            issues.append((ln, 0, "warning", "External URLs (fonts, imports, images) are blocked by the policy. Host assets on this site or use data: URLs."))
        if s and not s.endswith(("{", "}", ",", ";", "*/")) and ":" in s and not s.startswith(("@", "/")) and "{" not in s \
                and depth > 0 and not re.match(r"^[\w.#:\[\]>+~*\- ,=\"'()%]+$", s.split(":")[0] + "x"):
            pass
        if depth > 0 and re.match(r"^[a-z-]+\s*:\s*[^;{}]+$", s) and not s.endswith(";"):
            nxt = next((l.strip() for l in text.splitlines()[ln + 1:] if l.strip()), "")
            if nxt and not nxt.startswith("}"):
                issues.append((ln, len(line), "error", "Missing ';' at the end of this declaration."))
    for ln, col in stack[max(0, len(stack) - depth):] if depth > 0 else []:
        issues.append((ln, col, "error", "This '{' is never closed."))
    for m in re.finditer(r":[^;{}\n:]+\n\s*[-a-z]+\s*:", text):
        ln = text.count("\n", 0, m.start())
        issues.append((ln, 0, "error", "Missing ';' at the end of this declaration."))
    if re.search(r"\bexpression\s*\(", text):
        issues.append((0, 0, "warning", "expression() is obsolete and unsafe."))
    return sorted(issues, key=lambda i: (i[0], i[1]))


def check_browser_js(code: str) -> list[Issue]:
    issues: list[Issue] = []
    src = code or ""
    if src.strip():
        res = jscheck.check(src)
        if res:
            issues.append((res[0], res[1], "error", res[2]))
    stripped = re.sub(r"(?<![:\w'\"`/])//[^\n]*|/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.S)
    for ln, line in enumerate(stripped.splitlines()):
        for pat, sev, msg in (
            (r"\brequire\s*\(|\bmodule\.exports\b|\bexports\.", "error", "require / module.exports are Node.js. This code runs in the browser — use fetch() to talk to your backend."),
            (r"\bprocess\.(?:env|exit|cwd)", "error", "`process` does not exist in the browser. Secrets must stay on the backend."),
            (r"discord\.js|GatewayIntentBits|new\s+Client\s*\(|\bREST\s*\(", "error", "discord.js cannot run in the browser. Call your backend API (a Web Route) instead."),
            (r"\beval\s*\(|new\s+Function\s*\(", "warning", "eval / new Function are blocked by the Content-Security-Policy."),
            (r"\.innerHTML\s*[+]?=", "warning", "innerHTML with user data is an XSS risk. Prefer textContent or createElement."),
            (r"document\.write\s*\(", "warning", "document.write blocks parsing and is discouraged."),
            (r"(?:fetch|WebSocket)\s*\(\s*['\"`](?:https?|wss?)://", "warning", "Requests to other origins are blocked (connect-src 'self'). Use a relative path like '/api/custom/…'."),
            (r"\bimport\s+[\w{*]", "info", "ES-module imports are not supported here; the file is loaded as a classic script."),
        ):
            m = re.search(pat, line)
            if m:
                issues.append((ln, m.start(), sev, msg))
    if TOKEN_RE.search(src):
        issues.append((0, 0, "error", "This looks like a Discord bot token. Never put credentials in frontend code."))
    return sorted(issues, key=lambda i: (i[0], i[1]))


CHECKERS = {"html": check_html, "css": check_css, "browserjs": check_browser_js}


def check(lang: str, code: str) -> list[Issue]:
    return CHECKERS[lang](code)

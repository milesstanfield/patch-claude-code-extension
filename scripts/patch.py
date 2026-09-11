#!/usr/bin/env python3
"""Patch the Claude Code extension to stop locking editor groups, to open
new sessions in the active editor group instead of a new column, and to
reliably auto-focus the chat input when a new session opens.

Three fixes, applied to every anthropic.claude-code-* install under
~/.cursor/extensions and ~/.vscode/extensions:

1. Lock (extension.js): neutralize `workbench.action.lockEditorGroup` when
   the panel opens (`if(X)` -> `if(X&&!1)`).
2. Column (extension.js): replace the `findUnusedColumn()` /
   `ViewColumn.Beside` fallback used when no Claude Code panel is already
   open with `{alias}.ViewColumn.Active||1`, so "+ New session" lands as a
   new tab in whatever editor group is currently active instead of
   splitting a new column beside it. The minified vscode import alias
   varies by version (e.g. Tt, It, Rt, Nt, Lt, B4) and is detected from the
   createWebviewPanel call that follows.
3. Focus (webview/index.js): the chat webview already tries to focus its
   input whenever the session id changes (including for a brand-new
   panel), but it's gated on `ambientFocusAllowed()` (= `document.hasFocus()`),
   which for a freshly created panel may never become true within any
   reasonable window (it depends on VS Code actually handing the iframe
   real DOM focus, which doesn't reliably happen just because the panel
   is the visible/active tab). This drops that gate and just calls
   `.focus()` on the composer directly on a short retry schedule (to cover
   mount timing), while keeping the existing guard that avoids stealing
   focus from an already-focused input elsewhere in the panel.

Before the first write to a given file, it is backed up to <name>.bak
(never overwritten once it exists), so a patch can be undone with
`python3 scripts/restore.py` (or manually: `cp extension.js.bak
extension.js` / `cp webview/index.js.bak webview/index.js`).
"""

import re
import shutil
import sys
from pathlib import Path

EXTENSION_ROOTS = [
    Path.home() / ".cursor" / "extensions",
    Path.home() / ".vscode" / "extensions",
]

# Matches: if(E)await Pe.commands.executeCommand("workbench.action.lockEditorGroup")
LOCK_CALL = re.compile(
    r"if\((?P<cond>[A-Za-z$_][\w$]*)\)"
    r'(?P<call>await [A-Za-z$_][\w$]*\.commands\.executeCommand\("workbench\.action\.lockEditorGroup"\))'
)

LOCK_ALREADY = re.compile(
    r"if\([A-Za-z$_][\w$]*&&!1\)"
    r'await [A-Za-z$_][\w$]*\.commands\.executeCommand\("workbench\.action\.lockEditorGroup"\)'
)

# Matches the raw `this.findUnusedColumn()` call, the old
# `{alias}.ViewColumn.Beside||1` form, or the already-patched
# `{alias}.ViewColumn.Active||1` form -- anchored to the createWebviewPanel
# call that follows so the alias is read from a live reference rather than
# guessed from the matched text itself.
COLUMN_SITE = re.compile(
    r"(?:this\.findUnusedColumn\(\)"
    r"|[A-Za-z$_][\w$]*\.ViewColumn\.Beside\|\|1"
    r"|[A-Za-z$_][\w$]*\.ViewColumn\.Active\|\|1)"
    r"(?P<tail>,[A-Za-z$_][\w$]*=!0\}let [A-Za-z$_][\w$]*="
    r"(?P<alias>[A-Za-z$_][\w$]*)\.window\.createWebviewPanel)"
)

# Matches the single-shot "focus the composer if the session id changed and
# the webview already has focus" effect in webview/index.js. Anchors on
# identifiers that survive minification (ambientFocusAllowed, sessionId,
# document.activeElement, .current?.focus()) and captures the short local
# names for the useEffect hook, the "already-focused-elsewhere" guard, the
# focus attempt, the composer ref, the context object, and the session --
# all of which are just minifier-assigned locals and vary by version.
FOCUS_EFFECT = re.compile(
    r"(?P<useEffect>[A-Za-z$_][\w$]*)\(\(\)=>\{"
    r"if\((?P<activeCheck>[A-Za-z$_][\w$]*)\(document\.activeElement\)\)return;"
    r"(?P<attempt>[A-Za-z$_][\w$]*)\(\(\)=>(?P<ref>[A-Za-z$_][\w$]*)\.current\?\.focus\(\),"
    r"\(\)=>(?P<ctx>[A-Za-z$_][\w$]*)\.ambientFocusAllowed\(\)\)"
    r"\},\[(?P=ctx),(?P<sess>[A-Za-z$_][\w$]*)\.sessionId\.value\]\);"
)

# Matches this script's own first-cut (v1) patch, which still gated the
# retries on ambientFocusAllowed() -- kept so an install patched by an
# older copy of this script gets upgraded to the gate-free (v2) version
# instead of being skipped as "already patched".
FOCUS_V1 = re.compile(
    r"let ccTry=\(\)=>(?P<ref>[A-Za-z$_][\w$]*)\.current\?\.focus\(\);"
    r"if\((?P<ctx>[A-Za-z$_][\w$]*)\.ambientFocusAllowed\(\)\)ccTry\(\);"
    r"let ccTimers=\[300,800,1600\]\.map\(\(ccD\)=>setTimeout\(\(\)=>\{"
    r"if\((?P=ctx)\.ambientFocusAllowed\(\)\)ccTry\(\)"
    r"\},ccD\)\);"
    r"return \(\)=>ccTimers\.forEach\(clearTimeout\)"
)

FOCUS_V2_ALREADY = re.compile(
    r"let ccTry=\(\)=>[A-Za-z$_][\w$]*\.current\?\.focus\(\);ccTry\(\);"
    r"let ccTimers=\[50,150,300,600,1000,1600\]"
)

FOCUS_RETRY_DELAYS = "[50,150,300,600,1000,1600]"


def _focus_body(ref: str) -> str:
    return (
        f"let ccTry=()=>{ref}.current?.focus();ccTry();"
        f"let ccTimers={FOCUS_RETRY_DELAYS}.map((ccD)=>setTimeout(ccTry,ccD));"
        f"return ()=>ccTimers.forEach(clearTimeout)"
    )


def backup(extension_js: Path) -> Path:
    bak = extension_js.with_suffix(extension_js.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(extension_js, bak)
    return bak


def patch_lock(src: str, parts: list[str]) -> str:
    if LOCK_ALREADY.search(src):
        parts.append("lock already patched")
        return src
    patched, count = LOCK_CALL.subn(r"if(\g<cond>&&!1)\g<call>", src)
    if count == 0:
        parts.append("lock PATTERN NOT FOUND")
        return src
    parts.append(f"lock patched ({count})")
    return patched


def patch_column(src: str, parts: list[str]) -> str:
    m = COLUMN_SITE.search(src)
    if m is None:
        parts.append("column PATTERN NOT FOUND")
        return src

    alias = m.group("alias")
    current = m.group(0)[: -len(m.group("tail"))]
    desired = f"{alias}.ViewColumn.Active||1"

    if current == desired:
        parts.append("column already patched")
        return src
    if len(current) != len(desired):
        parts.append(f"column PATTERN NOT FOUND (alias {alias!r} wrong length)")
        return src

    start = m.start()
    parts.append(f"column patched ({alias}, was {current!r})")
    return src[:start] + desired + src[start + len(current) :]


def patch_focus(src: str, parts: list[str]) -> str:
    if FOCUS_V2_ALREADY.search(src):
        parts.append("focus already patched")
        return src

    if FOCUS_V1.search(src):
        patched, count = FOCUS_V1.subn(lambda m: _focus_body(m.group("ref")), src)
        parts.append(f"focus upgraded from older patch ({count})")
        return patched

    def repl(m: re.Match) -> str:
        ue, ac, ref, ctx, sess = (
            m.group("useEffect"),
            m.group("activeCheck"),
            m.group("ref"),
            m.group("ctx"),
            m.group("sess"),
        )
        return f"{ue}(()=>{{if({ac}(document.activeElement))return;{_focus_body(ref)}}},[{ctx},{sess}.sessionId.value]);"

    patched, count = FOCUS_EFFECT.subn(repl, src)
    if count == 0:
        parts.append("focus PATTERN NOT FOUND")
        return src
    parts.append(f"focus patched ({count})")
    return patched


def patch_extension_js(extension_js: Path) -> str:
    src = extension_js.read_text()
    original = src
    parts: list[str] = []

    src = patch_lock(src, parts)
    src = patch_column(src, parts)

    if src != original:
        bak = backup(extension_js)
        extension_js.write_text(src)
        parts.append(f"backup at {bak} (restore with: python3 scripts/restore.py)")

    return "; ".join(parts)


def patch_webview_js(webview_js: Path) -> str:
    src = webview_js.read_text()
    original = src
    parts: list[str] = []

    src = patch_focus(src, parts)

    if src != original:
        bak = backup(webview_js)
        webview_js.write_text(src)
        parts.append(f"backup at {bak} (restore with: python3 scripts/restore.py)")

    return "; ".join(parts)


def main() -> int:
    found_any = False
    failures = 0
    for root in EXTENSION_ROOTS:
        if not root.is_dir():
            continue
        for ext_dir in sorted(root.glob("anthropic.claude-code-*")):
            extension_js = ext_dir / "extension.js"
            if not extension_js.is_file():
                print(f"{ext_dir.name}: extension.js missing, skipped")
            else:
                found_any = True
                result = patch_extension_js(extension_js)
                print(f"{ext_dir.name} ({root}) extension.js: {result}")
                if "NOT FOUND" in result:
                    failures += 1

            webview_js = ext_dir / "webview" / "index.js"
            if not webview_js.is_file():
                print(f"{ext_dir.name}: webview/index.js missing, skipped")
                continue
            found_any = True
            result = patch_webview_js(webview_js)
            print(f"{ext_dir.name} ({root}) webview/index.js: {result}")
            if "NOT FOUND" in result:
                failures += 1
    if not found_any:
        print("No anthropic.claude-code-* extension installations found.")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

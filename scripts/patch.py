#!/usr/bin/env python3
"""Patch the Claude Code extension to stop locking editor groups, to open
new sessions in the active editor group instead of a new column, and to
reliably auto-focus the chat input when a new session opens.

Three fixes, applied to every anthropic.claude-code-* install under
~/.cursor/extensions and ~/.vscode/extensions:

1. Lock (extension.js): replace every
   `{alias}.commands.executeCommand("workbench.action.lockEditorGroup")`
   expression with `Promise.resolve()`. Earlier versions neutralized the
   guarding condition instead (`if(X)` -> `if(X&&!1)`), which had to be
   re-taught the statement's shape on every bundle change and, in 2.1.270,
   silently missed the real one: the call had been hoisted into a helper
   (`function d6$(){return h$.commands.executeCommand(...)}`) with three
   callers, leaving only a rare inline site for the old pattern to "fix".
   Killing the call expression itself covers every caller and every shape.
2. Column (extension.js): replace the `findUnusedColumn()` /
   `ViewColumn.Beside` fallback used when no Claude Code panel is already
   open with `{alias}.ViewColumn.Active||1`, so "+ New session" lands as a
   new tab in whatever editor group is currently active instead of
   splitting a new column beside it. The minified vscode import alias
   varies by version (e.g. Tt, It, Rt, Nt, Lt, B4) and is detected from the
   createWebviewPanel call that follows (v1 shape, through 2.1.268) or from
   the `!==alias.ViewColumn.Beside` comparison in the same statement (v2
   shape, 2.1.269+, after the fallback was rewritten into an if/else
   chain).

   The v2 shape also assigns the `startedInNewColumn` flag right after the
   column choice (`{z}={w}!==alias.ViewColumn.Beside`), and that has to be
   pinned to `!1` as part of the same edit. `ViewColumn.Active` is -1 and
   `Beside` is -2, so swapping in Active while leaving the comparison alone
   evaluates to true -- the patch ends up announcing "I opened a brand-new
   column", which is precisely what fix 1 is trying to stop. (In 2.1.270 that
   flag has no other consumer than the lock decision.) Left unpinned, the
   two fixes fight: the active group gets locked, and because a locked group
   can't accept new tabs, the *next* session is pushed into a separate group
   of its own -- exactly the behavior the column fix exists to prevent.
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

Every run restores a file from its .bak first (if one exists) before
re-patching, so patterns are always matched against the pristine original
rather than against whatever a previous pass left behind -- this is what
makes "already patched" a live check instead of a stale assumption, and
means it's always safe to just run this script after an update instead of
checking first.

If a pattern still can't be matched, nothing is written -- the file is left
exactly as found, and it's copied into this repo's git-ignored tmp/
directory (Claude Code's sandbox can't read ~/.vscode/extensions or
~/.cursor/extensions directly, but it can read this repo) so Claude can
inspect it directly and re-teach the pattern.
"""

import re
import shutil
import sys
from pathlib import Path

EXTENSION_ROOTS = [
    Path.home() / ".cursor" / "extensions",
    Path.home() / ".vscode" / "extensions",
]

TMP_DIR = Path(__file__).parent.parent / "tmp"

# Every lock site is ultimately the same expression:
# `{alias}.commands.executeCommand("workbench.action.lockEditorGroup")`.
# Earlier versions of this script tried to neutralize the *condition* guarding
# that call (`if(X)` -> `if(X&&!1)`), which meant chasing the shape of the
# surrounding statement every time the bundle changed -- and silently missing
# sites. 2.1.270 hoisted the call into a helper (`function d6$(){return
# h$.commands.executeCommand("workbench.action.lockEditorGroup")}`) with three
# callers, so condition-matching found only the one remaining inline site (a
# rare remembered-tab-reveal failure path) and reported success while the
# "+ New session" path kept locking.
#
# So: replace the call expression itself, everywhere it appears. The command
# has exactly one purpose -- locking the group Claude Code opened -- so killing
# all of them is what we want, and `Promise.resolve()` keeps every call site
# valid whether it's awaited, returned, or neither.
LOCK_COMMAND_CALL = re.compile(
    r'[A-Za-z$_][\w$]*\.commands\.executeCommand\("workbench\.action\.lockEditorGroup"\)'
)

LOCK_COMMAND_NAME = "workbench.action.lockEditorGroup"

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

# 2.1.269+ rewrote the fallback into an if/else assignment chain (first try
# reusing an existing tab via a helper call, only falling back to
# findUnusedColumn() if that fails), which no longer ends in the old tail
# shape. Anchored instead on the `!==<alias>.ViewColumn.Beside` comparison
# that immediately follows the call in the same statement -- that's what
# supplies the alias, so there's no need to reach forward to
# createWebviewPanel like the v1 pattern does.
#
# The trailing `{z}={w}!==<alias>.ViewColumn.Beside` is the `startedInNewColumn`
# flag, and it must be rewritten too, not just read for its alias. Verified in
# 2.1.270 that this flag's ONLY consumer is the decision to lock the group, and
# `ViewColumn.Active` is -1 while `Beside` is -2 -- so swapping in Active while
# leaving the comparison alone leaves `z === true`, i.e. the patch itself tells
# the extension "I opened a brand-new column, go lock it". Pin it to `!1`: once
# the column is the active one, it is by definition not new.
#
# Also matches the half-patched shape (Active already swapped in, comparison
# left intact) that older copies of this script produced, so those installs get
# repaired rather than skipped as already patched.
COLUMN_CALL_V2 = re.compile(
    r"else (?P<w>[A-Za-z$_][\w$]*)=(?:this\.findUnusedColumn\(\)"
    r"|[A-Za-z$_][\w$]*\.ViewColumn\.Active\|\|1),"
    r"(?P<z>[A-Za-z$_][\w$]*)=(?P=w)!==(?P<alias>[A-Za-z$_][\w$]*)\.ViewColumn\.Beside"
)

COLUMN_ALREADY_V2 = re.compile(
    r"else [A-Za-z$_][\w$]*=[A-Za-z$_][\w$]*\.ViewColumn\.Active\|\|1,"
    r"[A-Za-z$_][\w$]*=!1"
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


# --- Post-conditions ------------------------------------------------------
#
# A regex matching something is NOT evidence the fix took. In 2.1.270 the lock
# call had been hoisted into a helper with three callers, and the old pattern
# matched a leftover inline site on a path that effectively never runs -- so the
# script reported "lock patched (1)" while "+ New session" kept locking the
# group. Same class of failure on the column side: a pattern said "already
# patched" while the startedInNewColumn flag was still wrong.
#
# So every fix states what must be TRUE of the finished file, and the result is
# checked against that before anything is written. If a future bundle moves the
# code again, these fail loudly instead of reporting a success that isn't real.

# v1 patched shape (through 2.1.268): the fallback is the last thing before the
# createWebviewPanel call. Its `=!0` flag is left alone -- harmless, since the
# lock fix removes the only thing that reads it.
COLUMN_PATCHED_V1 = re.compile(
    r"[A-Za-z$_][\w$]*\.ViewColumn\.Active\|\|1,[A-Za-z$_][\w$]*=!0\}"
    r"let [A-Za-z$_][\w$]*=[A-Za-z$_][\w$]*\.window\.createWebviewPanel"
)


def verify_extension_js(src: str) -> list[str]:
    problems = []
    if LOCK_COMMAND_NAME in src:
        n = src.count(LOCK_COMMAND_NAME)
        problems.append(
            f"lock NOT NEUTRALIZED: {n} reference(s) to {LOCK_COMMAND_NAME} remain"
        )
    if "this.findUnusedColumn()" in src:
        problems.append("column NOT REDIRECTED: a this.findUnusedColumn() call remains")
    if not (COLUMN_PATCHED_V1.search(src) or COLUMN_ALREADY_V2.search(src)):
        problems.append(
            "column NOT REDIRECTED: no patched fallback found "
            "(expected ViewColumn.Active with the new-column flag pinned to !1)"
        )
    return problems


def verify_webview_js(src: str) -> list[str]:
    problems = []
    if not FOCUS_V2_ALREADY.search(src):
        problems.append("focus NOT APPLIED: patched retry loop not found")
    return problems


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


def restore_if_backed_up(path: Path, parts: list[str]) -> None:
    """Reset to the pristine .bak, if one exists, before patching.

    Patterns are then always matched against the original file rather than
    against however a previous pass left it -- so a stale or half-applied
    state can never be mistaken for "already patched".
    """
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.is_file():
        shutil.copy2(bak, path)
        parts.append("restored from backup")


def stash(path: Path, ext_dir_name: str, dest_name: str) -> Path:
    dest = TMP_DIR / ext_dir_name / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def patch_lock(src: str, parts: list[str]) -> str:
    patched, count = LOCK_COMMAND_CALL.subn("Promise.resolve()", src)
    if count:
        parts.append(f"lock patched ({count} call site(s))")
        return patched
    if LOCK_COMMAND_NAME in src:
        parts.append("lock PATTERN NOT FOUND")
        return src
    parts.append("lock already patched")
    return src


def patch_column(src: str, parts: list[str]) -> str:
    m = COLUMN_SITE.search(src)
    if m is not None:
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

    if COLUMN_ALREADY_V2.search(src):
        parts.append("column already patched")
        return src

    m2 = COLUMN_CALL_V2.search(src)
    if m2 is not None:
        alias, w, z = m2.group("alias"), m2.group("w"), m2.group("z")
        start, end = m2.span()
        replacement = f"else {w}={alias}.ViewColumn.Active||1,{z}=!1"
        parts.append(f"column patched ({alias}, was {m2.group(0)!r})")
        return src[:start] + replacement + src[end:]

    parts.append("column PATTERN NOT FOUND")
    return src


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
    parts: list[str] = []
    restore_if_backed_up(extension_js, parts)

    src = original = extension_js.read_text()
    src = patch_lock(src, parts)
    src = patch_column(src, parts)

    return _finish(extension_js, original, src, parts, verify_extension_js(src))


def patch_webview_js(webview_js: Path) -> str:
    parts: list[str] = []
    restore_if_backed_up(webview_js, parts)

    src = original = webview_js.read_text()
    src = patch_focus(src, parts)

    return _finish(webview_js, original, src, parts, verify_webview_js(src))


def _finish(
    path: Path, original: str, src: str, parts: list[str], problems: list[str]
) -> str:
    """Write the patched file only if the result passes its post-conditions.

    A failed check means the bundle moved and the patterns need re-teaching, so
    the file is left exactly as found rather than written in a half-patched
    state -- which is what made the last breakage hard to see.
    """
    if problems:
        return "VERIFY FAILED -- not written: " + "; ".join(problems) + (
            f" [patch step reported: {'; '.join(parts)}]" if parts else ""
        )

    if src != original:
        bak = backup(path)
        parts.append(f"backup at {bak} (restore with: python3 scripts/restore.py)")
        path.write_text(src)

    parts.append("VERIFIED")
    return "; ".join(parts)


FAILED_MARKERS = ("NOT FOUND", "VERIFY FAILED")


def failed(result: str) -> bool:
    return any(marker in result for marker in FAILED_MARKERS)


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
                if failed(result):
                    failures += 1
                    dest = stash(extension_js, ext_dir.name, "extension.js")
                    result += f"; copied to {dest} for Claude"
                print(f"{ext_dir.name} ({root}) extension.js: {result}")

            webview_js = ext_dir / "webview" / "index.js"
            if not webview_js.is_file():
                print(f"{ext_dir.name}: webview/index.js missing, skipped")
                continue
            found_any = True
            result = patch_webview_js(webview_js)
            if failed(result):
                failures += 1
                dest = stash(webview_js, ext_dir.name, "webview-index.js")
                result += f"; copied to {dest} for Claude"
            print(f"{ext_dir.name} ({root}) webview/index.js: {result}")

    if not found_any:
        print("No anthropic.claude-code-* extension installations found.")
        return 1

    if failures:
        print(
            f"\n{failures} file(s) NOT PATCHED. The extension bundle changed shape and "
            "patch.py's patterns need updating -- nothing was written for those files, "
            "so the install is exactly as it was. The unpatched file(s) were copied into "
            f"{TMP_DIR} -- tell Claude patching failed and it can read them directly and "
            "re-teach the patterns."
        )
    else:
        print("\nAll fixes applied and verified. Fully quit VS Code/Cursor to load them.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

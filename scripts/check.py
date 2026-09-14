#!/usr/bin/env python3
"""Read-only check: report whether patch.py's patterns still match the
installed anthropic.claude-code-* extension(s), without modifying anything.

Run this after every extension auto-update, before running patch.py, so you
know whether the patch would actually apply cleanly instead of finding out
by running it "blindly". Calls patch.py's own patch_lock/patch_column/
patch_focus functions directly (on a throwaway copy of the source, never
written back) instead of keeping a second copy of the matching logic here
-- so this can't silently drift from what patch.py actually matches.

Claude Code's own sandbox generally can't read ~/.vscode/extensions or
~/.cursor/extensions directly, so if any pattern comes back NOT FOUND,
this automatically copies the affected file(s) into this repo's
git-ignored tmp/ directory, which Claude *can* read -- so there is nothing
else to run and nothing to paste; just tell Claude a pattern wasn't found.

Exit code: 0 if every fix in every found file is either patchable or
already patched, 1 if anything is NOT FOUND (or no install was found).
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from patch import (  # noqa: E402
    EXTENSION_ROOTS,
    patch_column,
    patch_focus,
    patch_lock,
    verify_extension_js,
    verify_webview_js,
)

TMP_DIR = Path(__file__).parent.parent / "tmp"


def stash(src: Path, ext_dir_name: str, dest_name: str) -> Path:
    dest = TMP_DIR / ext_dir_name / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def main() -> int:
    found_any = False
    failures = 0
    for root in EXTENSION_ROOTS:
        if not root.is_dir():
            continue
        for ext_dir in sorted(root.glob("anthropic.claude-code-*")):
            extension_js = ext_dir / "extension.js"
            if extension_js.is_file():
                found_any = True
                src = extension_js.read_text()
                parts: list[str] = []
                out = patch_lock(src, parts)
                out = patch_column(out, parts)
                problems = verify_extension_js(out)
                status = "; ".join(parts + [
                    "would VERIFY FAILED: " + "; ".join(problems) if problems
                    else "would verify clean"
                ])
                if problems or "NOT FOUND" in status:
                    failures += 1
                    dest = stash(extension_js, ext_dir.name, "extension.js")
                    status += f"; copied to {dest} for Claude"
                print(f"{ext_dir.name} ({root}) extension.js: {status}")
            else:
                print(f"{ext_dir.name}: extension.js missing, skipped")

            webview_js = ext_dir / "webview" / "index.js"
            if webview_js.is_file():
                found_any = True
                src = webview_js.read_text()
                parts = []
                out = patch_focus(src, parts)
                problems = verify_webview_js(out)
                status = "; ".join(parts + [
                    "would VERIFY FAILED: " + "; ".join(problems) if problems
                    else "would verify clean"
                ])
                if problems or "NOT FOUND" in status:
                    failures += 1
                    dest = stash(webview_js, ext_dir.name, "webview-index.js")
                    status += f"; copied to {dest} for Claude"
                print(f"{ext_dir.name} ({root}) webview/index.js: {status}")
            else:
                print(f"{ext_dir.name}: webview/index.js missing, skipped")

    if not found_any:
        print("No anthropic.claude-code-* extension installations found.")
        return 1
    if failures:
        print(
            f"\n{failures} file(s) would NOT patch cleanly -- copied into tmp/. "
            "Tell Claude patching would fail; it can read tmp/ directly, no need to paste anything."
        )
    else:
        print("\nAll fixes would apply and verify cleanly -- safe to run patch.py.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

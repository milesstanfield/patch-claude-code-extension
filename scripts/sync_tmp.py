#!/usr/bin/env python3
"""Copy each installed anthropic.claude-code-* extension's extension.js and
webview/index.js into this repo's (git-ignored) tmp/ directory.

Claude Code's own sandbox can read/write inside this repo but generally
can't read ~/.vscode/extensions or ~/.cursor/extensions directly. Running
this after an extension auto-update lets Claude inspect the new bundle
(grep it, diff it against a previous tmp/ copy, etc.) without you having to
paste terminal output back into the chat.

Usage: python3 scripts/sync_tmp.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from patch import EXTENSION_ROOTS  # noqa: E402

TMP_DIR = Path(__file__).parent.parent / "tmp"


def main() -> int:
    copied = 0
    for root in EXTENSION_ROOTS:
        if not root.is_dir():
            continue
        for ext_dir in sorted(root.glob("anthropic.claude-code-*")):
            dest = TMP_DIR / ext_dir.name
            dest.mkdir(parents=True, exist_ok=True)

            extension_js = ext_dir / "extension.js"
            if extension_js.is_file():
                shutil.copy2(extension_js, dest / "extension.js")
                copied += 1
                print(f"copied {extension_js} -> {dest / 'extension.js'}")

            webview_js = ext_dir / "webview" / "index.js"
            if webview_js.is_file():
                shutil.copy2(webview_js, dest / "webview-index.js")
                copied += 1
                print(f"copied {webview_js} -> {dest / 'webview-index.js'}")

            package_json = ext_dir / "package.json"
            if package_json.is_file():
                shutil.copy2(package_json, dest / "package.json")
                copied += 1
                print(f"copied {package_json} -> {dest / 'package.json'}")

    if copied == 0:
        print("No anthropic.claude-code-* extension installations found.")
        return 1
    print(f"\n{copied} file(s) copied into {TMP_DIR} for Claude to inspect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

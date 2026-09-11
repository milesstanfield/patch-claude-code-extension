# patch-claude-code-lock

Scripts that patch the Claude Code (`anthropic.claude-code`) VS Code/Cursor extension's minified `extension.js` and `webview/index.js` to fix editor-group locking, change where new sessions open, and make new-session auto-focus reliable.

## What it fixes

There is no setting for any of this — the only fix is patching the obfuscated extension files on the local machine:

- [anthropics/claude-code#80148](https://github.com/anthropics/claude-code/issues/80148) — extension programmatically locks the editor group (`workbench.action.lockEditorGroup`), bypassing `workbench.editor.autoLockGroups`
- [anthropics/claude-code#83333](https://github.com/anthropics/claude-code/issues/83333) — `findUnusedColumn()` creates an empty editor group (blank pane with only an X) when opening beside Cursor Agents
- Chat input isn't focused when a new session panel opens — the webview already tries to focus it when the session id changes, but the check is a single-shot `document.hasFocus()` test that loses a timing race against VS Code actually handing the new panel focus, so it silently gives up.

`scripts/patch.py` applies three fixes:

1. **Lock** (`extension.js`): neutralizes the `workbench.action.lockEditorGroup` call, so the group Claude Code opens into is never locked.
2. **Column** (`extension.js`): replaces the `findUnusedColumn()` / `ViewColumn.Beside` fallback (used when no Claude Code panel is already open) with `ViewColumn.Active`, so **"+ New session" opens as a new tab in your current active editor group** instead of splitting into a new column beside it.
3. **Focus** (`webview/index.js`): replaces the single-shot `document.hasFocus()` check with an inline retry (300/800/1600ms backoff), so the chat input reliably ends up focused once the new panel actually gains focus, instead of only when the timing happens to line up. The existing guard that avoids stealing focus from an already-focused input is kept.

Note the column fix is a deliberate behavior change, not just a bugfix — by default Claude Code opens beside your current group (which is what #83333's stray-empty-column bug was about); this patch goes further and stops it from opening a separate column at all.

Extension updates install into a fresh directory, so re-run the patch after every update.

## Usage

Clone this repo, then from its directory:

```bash
python3 scripts/patch.py
```

This finds every `anthropic.claude-code-*` install under `~/.cursor/extensions` and `~/.vscode/extensions` and applies the lock + column fixes to `extension.js` and the focus fix to `webview/index.js`. Already-patched pieces are skipped. Before its first write to a given file, it's backed up to `<name>.bak` (never overwritten once it exists), and the script's output tells you where the backup is and how to restore it.

After a successful patch:

- Reload the window (`Developer: Reload Window`). If the new behavior doesn't seem to take effect, fully quit VS Code/Cursor (not just reload) and reopen it — a reload doesn't always pick up the patched `extension.js`.
- Close any leftover empty group once, and unlock any already-locked group once (the patch only prevents future locks/empty columns).
- Close any Claude Code tabs/panels left open from before the patch. New sessions reuse an already-open Claude Code panel's column if one exists, so a pre-existing tab (especially one sitting in its own locked/separate column from before) will keep new sessions landing there instead of your active group. Starting from zero open Claude Code tabs is the reliable way to see the patched behavior.

### Reverting

```bash
python3 scripts/restore.py
```

Restores `extension.js` and `webview/index.js` from their `.bak` files for every install where a backup exists, undoing all three patches. Reload the window afterward.

## Supported versions

Verified lock + column patch against:

| Host | Versions |
| --- | --- |
| VS Code | `2.1.216`, `2.1.241`, `2.1.243`, `2.1.245`–`2.1.247`, `2.1.250`–`2.1.252`, `2.1.267` |
| Cursor | `2.1.238`, `2.1.239`, `2.1.263`, `2.1.266` |

The matcher treats minified locals as identifiers, not literals, so it covers both the 2.1.216 call site (`n=!0}let o=`) and later sites (`i=!0}let s=` and similar). Detected vscode aliases so far: `Nt` / `Lt` (2.1.238–239), `O4` (2.1.263), `B4` (2.1.266–267). Re-run after every extension update; if a later build reports `PATTERN NOT FOUND`, the bundle changed again.

Verified focus patch against VS Code `2.1.268` (`webview/index.js`). Same caveat applies: it anchors on the `ambientFocusAllowed`/`sessionId` identifiers and captures the minified locals around them, but a future bundle could still restructure that effect enough to need a re-check.

## License

[MIT](LICENSE)

"""Theme-aware introductory card for an Echo transcript."""

import html

import sublime


def _compact_workspace(path, segments=3):
    if not path:
        return "no workspace selected"
    normalized = str(path).replace("\\", "/").rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) <= segments:
        return normalized
    return "…/" + "/".join(parts[-segments:])


class WelcomePanel:
    def __init__(self, view, cwd):
        self._view = view
        self._cwd = cwd
        self._phantoms = sublime.PhantomSet(view, "echo_welcome")

    def update(self, cwd=None):
        if cwd is not None:
            self._cwd = cwd
        is_macos = sublime.platform() == "osx"
        shortcut = "⌘↵" if is_macos else "Ctrl+Enter"
        stop_shortcut = "⌘Esc" if is_macos else "Shift+Esc"
        workspace = self._cwd or "no workspace selected"
        visible_workspace = _compact_workspace(workspace)
        markup = (
            '<body id="echo-welcome"><style>'
            '.card{{margin:5px 0 5px;padding:13px 15px;'
            'border:1px solid color(var(--foreground) alpha(.14));'
            'border-radius:8px;'
            'background-color:color(var(--foreground) alpha(.035))}}'
            '.brand{{color:var(--accent);font-size:.78em;'
            'font-family:var(--font-mono);font-weight:bold;letter-spacing:.12em}}'
            '.title{{margin:5px 0 4px;font-size:1.18em;font-weight:bold}}'
            '.copy{{margin:0 0 10px;opacity:.76}}'
            '.workspace{{font-family:var(--font-mono);font-size:.82em;opacity:.72}}'
            '.hint{{margin-top:8px;font-size:.8em;opacity:.58}}'
            '.key{{color:var(--accent)}}'
            '</style><div class="card">'
            '<div class="brand">echo</div>'
            '<div class="title">workspace assistant</div>'
            '<div class="copy">ask questions, request edits, or add files with @.</div>'
            '<div class="workspace" title="{workspace}">⌂ {visible_workspace}</div>'
            '<div class="hint"><span class="key">{shortcut}</span> send'
            '&nbsp;&nbsp;·&nbsp;&nbsp;<span class="key">@</span> context'
            '&nbsp;&nbsp;·&nbsp;&nbsp;<span class="key">{stop_shortcut}</span> stop</div>'
            '</div></body>'
        ).format(
            workspace=html.escape(str(workspace)),
            visible_workspace=html.escape(visible_workspace),
            shortcut=shortcut,
            stop_shortcut=stop_shortcut,
        )
        self._phantoms.update([sublime.Phantom(
            sublime.Region(0, 0), markup, sublime.LAYOUT_BLOCK
        )])

    def clear(self):
        self._phantoms.update([])

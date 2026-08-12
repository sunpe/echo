"""Phantom controls attached to the live Echo composer."""

import html
from urllib.parse import urlparse

import sublime

from ...providers import normalize_provider_model, resolve_provider
from .chat_panel import StatusHint
from ...shared.settings import (
    ECHO_APPROVE_MODE,
    ECHO_CONNECTION_STATE,
    ECHO_MODEL,
    ECHO_PLAN_MODE,
)
from .ui_components import ApproveMode, PlanMode, get_input_start, input_editable_start


_CONNECTED_STATES = frozenset(("connected", "ready"))
_STATE_LABELS = {
    "connected": "Connected",
    "ready": "Connected",
    "connecting": "Connecting",
    "initializing": "Connecting",
    "reconnecting": "Reconnecting",
    "failed": "Unavailable",
    "disconnected": "Offline",
    "closing": "Offline",
}
_APPROVAL_LABELS = {
    "default": "Ask",
    "allow-edit": "Edit",
    "accept-all": "All",
}


def _compact(value, limit=24):
    text = str(value)
    if len(text) <= limit:
        return text
    tail = text.rsplit("/", 1)[-1]
    if len(tail) <= limit:
        return tail
    return tail[:limit - 1] + "…"


def _endpoint(settings):
    try:
        provider = resolve_provider(settings)
    except Exception:
        return "unconfigured"
    if provider.name == "pi":
        return "local"
    hostname = urlparse(
        provider.config.get("app_server", {}).get("url", "")
    ).hostname
    return "local" if (hostname or "").lower() in {
        "localhost", "127.0.0.1", "::1"
    } else "remote"


class PromptGlyph:
    _MARKUP = (
        '<body id="echo-prompt-glyph" style="margin:0">'
        '<span style="color:var(--accent)">›</span>'
        '</body>'
    )

    def __init__(self, view):
        self._view, self._phantoms = (
            view, sublime.PhantomSet(view, "echo_prompt_glyph")
        )

    def update(self):
        position = input_editable_start(self._view)
        if position <= self._view.size():
            self._phantoms.update([sublime.Phantom(
                sublime.Region(position, position),
                self._MARKUP,
                sublime.LAYOUT_INLINE,
            )])

    def clear(self):
        self._phantoms.update([])


class ComposerControls:
    def __init__(self, view, window, session_lookup=None):
        self.view, self.window, self.session_lookup = view, window, session_lookup
        self.phantom_set, self.status_hint = (
            sublime.PhantomSet(view, "echo_composer_controls"), StatusHint()
        )

    def navigate(self, target):
        command = {
            "model": "echo_chat_set_model",
            "plan": "echo_chat_toggle_plan_mode",
            "approve": "echo_chat_set_approve_mode",
            "stop": "echo_chat_interrupt",
            "stop_conversation": "echo_chat_interrupt",
        }.get(target)
        if command:
            arguments = {"confirm": True} \
                if target in ("stop", "stop_conversation") else None
            self.window.run_command(command, arguments) if arguments \
                else self.window.run_command(command)

    def set_running(self, active):
        changed = self.status_hint.change_visibility(active)
        if changed:
            self.update()

    def set_stopping(self, stopping, text=None):
        changed = self.status_hint.change_stop_state(stopping, text)
        if self.status_hint.visible and changed:
            self.update()

    def update(self):
        values = self._values()
        controls = "&nbsp;".join(
            '<a href="{key}" class="chip" title="{title}">'
            '<span class="chip-label">{label}</span> {value}</a>'.format(
                key=key,
                label={
                    "model": "Model",
                    "plan": "Plan",
                    "approve": "Approval",
                }[key],
                value=html.escape(_compact(value)),
                title=html.escape(str(value), quote=True),
            )
            for key, value in (
                ("model", values["model"]),
                ("plan", values["plan"]),
                ("approve", values["approve"]),
            )
        )
        stop_control = self.status_hint.render()
        if stop_control:
            stop_control = "&nbsp;" + stop_control
        markup = (
            '<body id="echo-composer"><style>'
            '.panel{{margin:2px 0 3px;padding:8px 2px 6px;'
            'border-top:1px solid color(var(--foreground) alpha(.13));'
            'border-bottom:1px solid color(var(--foreground) alpha(.13))}}'
            '.status-row{{margin-bottom:7px}}'
            '.source{{opacity:.68;font-family:var(--font-mono);font-size:.8em}}'
            '.state{{font-size:.8em;font-weight:600}}'
            '.connection-ok{{color:var(--greenish)}}'
            '.connection-error{{color:var(--redish)}}'
            '.actions{{margin-bottom:7px}}'
            '.chip{{display:inline-block;margin-bottom:4px;padding:4px 7px;'
            'text-decoration:none;border:1px solid '
            'color(var(--accent) alpha(.24));border-radius:5px;'
            'background-color:color(var(--accent) alpha(.07))}}'
            '.chip-label{{opacity:.5;font-size:.72em;font-weight:bold}}'
            '.stop-hint{{display:inline-block;margin:0 0 5px;padding:4px 7px;'
            'color:var(--redish);text-decoration:none}}'
            '</style><div class="panel">'
            '<div class="status-row"><span class="source">{provider}'
            '&nbsp;&nbsp;·&nbsp;&nbsp;{endpoint}</span>'
            '&nbsp;&nbsp;<span class="state {state_class}">● {state_label}</span></div>'
            '<div class="actions">{controls}{stop}</div>'
            '</div></body>'
        ).format(controls=controls, stop=stop_control, **values)
        point = get_input_start(self.view)
        self.phantom_set.update([sublime.Phantom(
            sublime.Region(point, point), markup, sublime.LAYOUT_BLOCK,
            self.navigate,
        )])

    def _values(self):
        package = sublime.load_settings("echo.sublime-settings")
        provider = str(package.get("provider", "codex"))
        model = normalize_provider_model(
            self.window.settings().get(ECHO_MODEL)
        )
        if not model and self.session_lookup:
            session = self.session_lookup()
            agent = getattr(getattr(session, "agent_thread", None), "agent", None)
            model = normalize_provider_model(
                getattr(getattr(agent, "options", None), "model", None)
            )
        try:
            plan = PlanMode(self.window.settings().get(
                ECHO_PLAN_MODE, PlanMode.FAST.value
            ))
        except ValueError:
            plan = PlanMode.FAST
        try:
            approve = ApproveMode(self.window.settings().get(
                ECHO_APPROVE_MODE, ApproveMode.ALLOW_EDIT.value
            ))
        except (TypeError, ValueError):
            approve = ApproveMode.ALLOW_EDIT
        state = str(self.view.settings().get(
            ECHO_CONNECTION_STATE, "disconnected"
        ))
        normalized_state = state.lower()
        return {
            "provider": html.escape(provider),
            "endpoint": _endpoint(package),
            "state": html.escape(state),
            "state_label": html.escape(_STATE_LABELS.get(
                normalized_state, state.replace("_", " ").title()
            )),
            "state_class": "connection-ok" if normalized_state in _CONNECTED_STATES
            else "connection-error",
            "model": model or "default",
            "plan": "On" if plan is PlanMode.PLANNING else "Off",
            "approve": _APPROVAL_LABELS.get(approve.value, approve.value),
        }

    def clear(self):
        self.phantom_set.update(list())

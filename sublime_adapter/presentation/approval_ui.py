"""Approval-card presentation and preview navigation."""

from dataclasses import dataclass
import html
import os

import sublime

from .. import diff_view
from ...shared.settings import ECHO_PLAN_REQUEST_ID
from .ui_components import ApproveMode, get_input_start


@dataclass(frozen=True)
class ApprovalContent:
    markup: str
    preview: object = None
    plan: str = ""


class ApprovalCard:
    _TITLES = {
        "command_execution": "Command execution approval",
        "fileChange": "File change approval",
        "CodexImplementPlan": "Plan implementation approval",
    }

    @classmethod
    def content(cls, tool, arguments, read_file=None):
        read_file = read_file or cls._read
        if tool in ("Edit", "Write"):
            path = arguments.get("file_path", "")
            name = os.path.basename(path) or "new_file"
            old = arguments.get("old_string", "") if tool == "Edit" \
                else read_file(path)
            new = arguments.get("new_string", "") if tool == "Edit" \
                else arguments.get("content", "")
            action = "Edit file" if tool == "Edit" else "Write file"
            return ApprovalContent(
                cls._action(action) + "<br>" + cls._link(name),
                (old, new, name),
            )
        if tool == "CodexImplementPlan":
            plan = arguments.get("plan", "")
            headline = plan.splitlines()[0] if plan else "Empty Plan"
            return ApprovalContent(
                cls._action("Implement plan") + "<br>"
                + cls._link("plan", "show_plan") + "<br>" + cls._safe(headline),
                ("", plan, "Implementation Plan"),
                plan,
            )
        if tool == "command_execution":
            command = cls._command(arguments) or "Command details unavailable"
            details = cls._details(
                ("Working directory", arguments.get("cwd")),
                ("Reason", arguments.get("reason")),
            )
            return ApprovalContent(
                cls._action("Execute command")
                + '<div class="command">{}</div>'.format(cls._safe(command))
                + details
            )
        if tool == "fileChange":
            diff = arguments.get("processed_diff") or {}
            if not diff:
                details = cls._details(
                    ("Request", arguments.get("itemId")),
                    ("Reason", arguments.get("reason")),
                    ("Write scope", arguments.get("grantRoot")),
                )
                return ApprovalContent(
                    cls._action("Modify files")
                    + "<br><small>File preview unavailable</small>"
                    + details
                )
            name = diff.get("display_name", "file")
            files = diff.get("files") or ()
            listing = "".join("<li>{}</li>".format(cls._safe(path)) for path in files[:5])
            return ApprovalContent(
                cls._action("Modify files") + "<br>" + cls._link(name)
                + ("<ul>" + listing + "</ul>" if listing else "")
                + cls._details(
                    ("Reason", arguments.get("reason")),
                    ("Write scope", arguments.get("grantRoot")),
                ),
                (diff.get("old_text", ""), diff.get("new_text", ""), name),
            )
        rows = [
            "{}: {}".format(cls._safe(key), cls._safe(value))
            for key, value in arguments.items() if isinstance(value, str)
        ]
        return ApprovalContent(
            cls._action(tool or "Unknown action")
            + ("<br>" + "<br>".join(rows) if rows else "")
        )

    @classmethod
    def render(cls, request_id, tool, content, mode=None):
        labels = [("allow", "Implement" if tool == "CodexImplementPlan" else "Allow"),
                  ("deny", "Deny")]
        if mode in (ApproveMode.DEFAULT.value, ApproveMode.ALLOW_EDIT.value):
            labels.append(("allow_chat", "Allow for chat"))
        links = [
            '<a href="{}" class="{}">{}</a>'.format(action, action, label)
            for action, label in labels
        ]
        actions = " ".join(links[:2])
        if len(links) > 2:
            actions += '<div class="secondary-action">{}</div>'.format(links[2])
        return (
            '<body id="echo-approval-{id}"><style>'
            '.card{{margin:10px 0;padding:10px;color:var(--foreground);'
            'border-left:3px solid var(--accent)}}'
            '.title{{font-weight:bold;color:var(--foreground)}}'
            '.content{{margin:8px 0 12px}}'
            '.command{{margin:6px 0;font-family:var(--font-mono);'
            'white-space:pre-wrap}}'
            '.content a{{color:var(--accent)}}'
            '.actions a{{display:inline-block;padding:4px 8px;color:var(--foreground);'
            'white-space:nowrap;text-decoration:none;border:1px solid;border-radius:3px}}'
            '.secondary-action{{margin-top:7px}}'
            '.actions .allow,.actions .allow_chat{{'
            'background-color:color(var(--greenish) alpha(.15));'
            'border-color:color(var(--greenish) alpha(.45))}}'
            '.actions .deny{{background-color:color(var(--redish) alpha(.15));'
            'border-color:color(var(--redish) alpha(.45))}}'
            '</style><div class="card"><div class="title">{tool}</div>'
            '<div class="content">{body}</div><div class="actions">'
            '{actions}</div></div></body>'
        ).format(
            id=cls._safe(request_id),
            tool=cls._safe(cls._TITLES.get(tool, tool or "Approval required")),
            body=content.markup, actions=actions,
        )

    @classmethod
    def _command(cls, arguments):
        command = arguments.get("command")
        if isinstance(command, str) and command.strip():
            return command
        if isinstance(command, (list, tuple)):
            joined = " ".join(str(part) for part in command if part is not None)
            if joined.strip():
                return joined
        actions = arguments.get("commandActions") or arguments.get("parsedCmd") or ()
        if not isinstance(actions, (list, tuple)):
            return ""
        commands = [
            action.get("command") or action.get("cmd")
            for action in actions if isinstance(action, dict)
        ]
        return "\n".join(command for command in commands if command)

    @classmethod
    def _details(cls, *items):
        rows = [
            "<br><small>{}: {}</small>".format(cls._safe(label), cls._safe(value))
            for label, value in items if value not in (None, "")
        ]
        return "".join(rows)

    @classmethod
    def _action(cls, label):
        return "<b>Action:</b> {}".format(cls._safe(label))

    @staticmethod
    def _safe(value):
        return html.escape(str(value), quote=True)

    @classmethod
    def _link(cls, label, action="show_diff"):
        return '📄 <a href="{}">{}</a>'.format(action, cls._safe(label))

    @staticmethod
    def _read(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, TypeError):
            return ""


class ApprovalPanel:
    def __init__(self, view, window, on_action):
        self.view, self.window = view, window
        self._on_action, self._cards, self._previews = on_action, {}, {}

    def show(self, request_id, tool, arguments, approve_mode=None):
        content = ApprovalCard.content(tool, arguments)
        if content.preview is not None:
            self._previews[request_id] = content.preview
        if content.plan:
            sublime.set_timeout(
                lambda: self._open_plan(request_id, content.plan, True), 0
            )
        card = sublime.PhantomSet(
            self.view, "echo_approval_{}".format(request_id)
        )
        self._cards[request_id] = card
        card.update([sublime.Phantom(
            sublime.Region(get_input_start(self.view) - 1, get_input_start(self.view) - 1),
            ApprovalCard.render(request_id, tool, content, approve_mode),
            sublime.LAYOUT_BLOCK,
            lambda action: self._navigate(request_id, action),
        )])
        self.view.show(self.view.size())

    def _navigate(self, request_id, action):
        preview = self._previews.get(request_id)
        if action == "show_diff" and preview:
            diff_view.show_diff(self.window, *preview)
        elif action == "show_plan" and preview:
            if not self._focus_plan(request_id):
                self._open_plan(request_id, preview[1], False)
        else:
            self._on_action(request_id, action)

    def _focus_plan(self, request_id):
        match = next((view for view in self.window.views()
                      if view.settings().get(ECHO_PLAN_REQUEST_ID) == request_id), None)
        if match is None:
            return False
        self.window.focus_view(match)
        return True

    def _open_plan(self, request_id, plan, background):
        previous = self.window.active_view()
        self._create_plan_document(request_id, plan)
        if background and previous:
            self.window.focus_view(previous)

    def _create_plan_document(self, request_id, plan):
        view = self.window.new_file()
        settings = view.settings()
        settings.set(ECHO_PLAN_REQUEST_ID, request_id)
        view.set_name("Implementation Plan")
        view.set_scratch(True)
        view.set_syntax_file("Packages/Markdown/Markdown.sublime-syntax")
        view.run_command("append", {"characters": plan})
        return view

    def clear(self, request_id):
        card = self._cards.pop(request_id, None)
        if card:
            card.update([])
        self._previews.pop(request_id, None)

    def clear_all(self):
        cards, self._cards = self._cards, {}
        self._previews.clear()
        for card in cards.values():
            card.update([])

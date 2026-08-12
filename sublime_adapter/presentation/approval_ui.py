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
            return ApprovalContent(cls._link(name), (old, new, name))
        if tool == "CodexImplementPlan":
            plan = arguments.get("plan", "")
            headline = plan.splitlines()[0] if plan else "Empty Plan"
            return ApprovalContent(
                cls._link("plan", "show_plan") + "<br>" + cls._safe(headline),
                ("", plan, "Implementation Plan"),
                plan,
            )
        if tool == "command_execution":
            command = cls._safe(arguments.get("command", ""))
            cwd = arguments.get("cwd")
            detail = "<small>cwd: {}</small>".format(cls._safe(cwd)) if cwd else ""
            return ApprovalContent(command + ("<br>" + detail if detail else ""))
        if tool == "fileChange":
            diff = arguments.get("processed_diff") or {}
            if not diff:
                return ApprovalContent("file change without preview")
            name = diff.get("display_name", "file")
            files = diff.get("files") or ()
            listing = "".join("<li>{}</li>".format(cls._safe(path)) for path in files[:5])
            return ApprovalContent(
                cls._link(name) + ("<ul>" + listing + "</ul>" if listing else ""),
                (diff.get("old_text", ""), diff.get("new_text", ""), name),
            )
        rows = [
            "{}: {}".format(cls._safe(key), cls._safe(value))
            for key, value in arguments.items() if isinstance(value, str)
        ]
        return ApprovalContent("<br>".join(rows))

    @classmethod
    def render(cls, request_id, tool, content, mode=None):
        labels = [("allow", "Implement" if tool == "CodexImplementPlan" else "Allow"),
                  ("deny", "Deny")]
        if mode in (ApproveMode.DEFAULT.value, ApproveMode.ALLOW_EDIT.value):
            labels.append(("allow_chat", "Allow for chat"))
        actions = " ".join(
            '<a href="{}" class="{}">{}</a>'.format(action, action, label)
            for action, label in labels
        )
        return (
            '<body id="echo-approval-{id}"><style>'
            '.card{{margin:10px 0;padding:10px;border-left:3px solid var(--accent)}}'
            '.title{{font-weight:bold;color:var(--accent)}}'
            '.body{{margin:8px 0 12px;font-family:var(--font-mono)}}'
            '.card a{{padding:4px 8px;text-decoration:none;border-radius:3px}}'
            '.allow,.allow_chat{{background:var(--greenish);color:var(--background)}}'
            '.deny{{background:var(--redish);color:var(--background)}}'
            '</style><section class="card"><div class="title">{tool}</div>'
            '<div class="body">{body}</div><nav>{actions}</nav></section></body>'
        ).format(
            id=cls._safe(request_id), tool=cls._safe(tool),
            body=content.markup, actions=actions,
        )

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

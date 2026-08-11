"""Permission transactions shared by the chat UI and provider bridge."""

from dataclasses import dataclass
from enum import Enum


class PermissionRoute(Enum):
    SILENT_ALLOW = "silent_allow"
    CONFIRM = "confirm"
    QUESTION = "question"


@dataclass(frozen=True)
class PendingPermission:
    tool: str
    arguments: dict


class PermissionFlow:
    """Own pending requests and turn UI actions into protocol replies."""

    def __init__(self, send_reply, dismiss, implement_plan):
        self._send_reply = send_reply
        self._dismiss = dismiss
        self._implement_plan = implement_plan
        self._pending = {}
        self._allow_rest = False

    def reset(self):
        self._pending.clear()
        self._allow_rest = False

    def stage(self, request_id, tool, arguments):
        self._pending[request_id] = PendingPermission(
            tool, dict(arguments or {})
        )

    def open(self, request_id, tool, arguments, mode, local_policy=None):
        self.stage(request_id, tool, arguments)
        if tool == "AskUserQuestion":
            return PermissionRoute.QUESTION
        if self._can_skip_prompt(tool, mode, local_policy or {}):
            pending = self._pending.pop(request_id)
            self._send_reply(request_id, self._allow(pending.arguments))
            return PermissionRoute.SILENT_ALLOW
        return PermissionRoute.CONFIRM

    def decide(self, request_id, action):
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        try:
            if pending.tool == "CodexImplementPlan":
                if action == "allow":
                    self._implement_plan()
                return True
            if action == "allow_chat":
                self._allow_rest = True
            reply = (
                self._allow(pending.arguments)
                if action in ("allow", "allow_chat")
                else self._deny("User denied permission in Echo")
            )
            self._send_reply(request_id, reply)
            return True
        finally:
            self._dismiss(request_id)

    def answer(self, request_id, answers):
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        enriched = dict(pending.arguments)
        enriched["answers"] = dict(answers)
        self._send_reply(request_id, self._allow(enriched))
        self._dismiss(request_id)
        return True

    def cancel_question(self, request_id):
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        self._send_reply(request_id, self._deny("User cancelled selection"))
        self._dismiss(request_id)
        return True

    def _can_skip_prompt(self, tool, mode, local_policy):
        if self._allow_rest or mode == "accept-all":
            return True
        if tool.startswith("local_workspace."):
            local_name = tool.partition(".")[2]
            if local_name in local_policy.get("always_confirm", ()):
                return False
            return local_name in local_policy.get("auto_approve", ())
        return mode == "allow-edit" and tool == "fileChange"

    @staticmethod
    def _allow(arguments):
        return {"behavior": "allow", "updatedInput": arguments}

    @staticmethod
    def _deny(message):
        return {"behavior": "deny", "message": message}

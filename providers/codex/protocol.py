"""Codex app-server event, dynamic-tool, and approval handlers."""

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from ...domain.messages.message import AssistantMessage, Message, MessageType, TextBlock
from ...domain.changes.change_set import build_change_preview
from .compatibility import error_message, turn_id
from .tool_results import tool_result_content


LOG = logging.getLogger("echo")

_NOTIFICATION_ROUTES = {
    "thread/started": "_event_thread_started",
    "turn/started": "_event_turn_started",
    "turn/completed": "_event_turn_completed",
    "item/plan/delta": "_event_plan_delta",
    "item/agentMessage/delta": "_event_message_delta",
    "item/completed": "_handle_item_completed",
    "item/started": "_handle_item_started",
    "codex/event/stream_error": "_event_stream_error",
    "codex/event/error": "_event_error",
    "error": "_event_error",
}

_SERVER_REQUEST_ROUTES = {
    "item/commandExecution/requestApproval": "_handle_command_approval",
    "item/fileChange/requestApproval": "_handle_file_approval",
    "item/tool/requestUserInput": "_handle_request_user_input",
    "item/tool/call": "_handle_dynamic_tool_call",
}


def _command_started(item):
    command = item.get("command")
    actions = item.get("commandActions") or []
    if actions and isinstance(actions, list):
        command = actions[0].get("command") or command
    return Message(
        MessageType.TOOL_USE.value,
        content={
            "name": "command_execution",
            "command": command,
            "status": "in_progress",
        },
        id=item.get("id"),
    )


def _agent_message_completed(item):
    text = item.get("text", "")
    if not text:
        return None
    return AssistantMessage(
        content=[TextBlock(text)], id=item.get("id")
    )


def _file_change_completed(item):
    changes = item.get("changes") or []
    names = [
        os.path.basename(change["path"])
        for change in changes
        if change.get("path")
    ]
    return Message(
        MessageType.TOOL_USE.value,
        content={
            "name": "fileChange",
            "changes": changes,
            "filenames": names,
            "status": item.get("status"),
        },
        id=item.get("id"),
    )


def _mcp_call_completed(item):
    return Message(
        MessageType.TOOL_USE.value,
        content={
            "name": "{}/{}".format(
                item.get("server", ""), item.get("tool", "")
            ),
            "input": item.get("arguments") or {},
            "result": item.get("result") or {},
            "error": item.get("error") or {},
            "status": item.get("status"),
        },
        id=item.get("id"),
    )


def _reasoning_completed(item):
    summary = item.get("summary") or []
    if isinstance(summary, list):
        text = summary[0] if summary else ""
    else:
        text = str(summary)
    if not text:
        return None
    return Message(
        MessageType.THINKING.value, content=text, id=item.get("id")
    )


_STARTED_ITEM_CONVERTERS = {
    "commandExecution": _command_started,
}

_COMPLETED_ITEM_CONVERTERS = {
    "agentMessage": _agent_message_completed,
    "fileChange": _file_change_completed,
    "mcpToolCall": _mcp_call_completed,
    "reasoning": _reasoning_completed,
}


def _decision(approved):
    return {"decision": "accept" if approved else "decline"}


def _answer_payload(answers):
    formatted = {}
    for key, value in answers.items():
        if isinstance(value, str):
            values = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, list):
            values = value
        else:
            values = [str(value)]
        formatted[key] = {"answers": values}
    return {"answers": formatted}


_NONINTERACTIVE_ANSWER = (
    "This is an automated run. Continue with the reasonable recommended "
    "option without requesting interactive input."
)


def _automatic_answers(questions):
    answers = {}
    for question in questions:
        key = question.get("id") or question.get("question", "")
        answers[key] = {"answers": [_NONINTERACTIVE_ANSWER]}
    return {"answers": answers}


class ProtocolRouter:
    """Classify app-server envelopes before invoking domain handlers."""

    def __init__(self, owner):
        self.owner = owner

    async def dispatch(self, envelope):
        method = envelope.get("method")
        if method is None:
            self.owner._rpc.accept(envelope)
            return

        params = envelope.get("params") or {}
        handler_name = _SERVER_REQUEST_ROUTES.get(method)
        if handler_name is not None:
            request_id = envelope.get("id")
            if request_id is None:
                LOG.warning("Server request has no id: %s", method)
                return
            operation = getattr(self.owner, handler_name)(request_id, params)
            self.owner._spawn_server_request(operation)
            return

        handler_name = _NOTIFICATION_ROUTES.get(method)
        if handler_name is not None:
            await getattr(self.owner, handler_name)(params)


class CodexProtocolHandlersMixin:
    async def _dispatch(self, data: Dict[str, Any]) -> None:
        await self._protocol_router.dispatch(data)

    async def _publish(self, message: Message) -> None:
        await self._message_queue.put(message)

    async def _event_thread_started(self, params: Dict[str, Any]) -> None:
        thread_id = params.get("thread", {}).get("id")
        if thread_id:
            await self._publish(Message(
                "thread_started", {"session_id": thread_id}
            ))

    async def _event_turn_started(self, params: Dict[str, Any]) -> None:
        active_id = turn_id(params)
        self._active_turn_id = active_id
        self._turn_count += 1
        await self._publish(Message("turn_started", {
            "turnId": active_id,
            "turnIndex": self._turn_count,
        }))

    async def _event_turn_completed(self, params: Dict[str, Any]) -> None:
        completed_id = turn_id(params)
        if completed_id:
            self._active_turn_id = completed_id
        if self.plan_mode and self._plan_text:
            await self._publish(Message(
                MessageType.PLAN_DELTA.value, self._plan_text
            ))
            self._plan_text = ""
        await self._publish(Message(MessageType.STOP.value))

    async def _event_plan_delta(self, params: Dict[str, Any]) -> None:
        self._plan_text += params.get("delta", "")

    async def _event_message_delta(self, params: Dict[str, Any]) -> None:
        text = params.get("delta", "")
        if text:
            await self._publish(Message(
                MessageType.TEXT.value,
                text,
                id=params.get("itemId"),
            ))

    async def _event_stream_error(self, params: Dict[str, Any]) -> None:
        detail = params.get("msg", {})
        if isinstance(detail, dict):
            detail = detail.get("message", "")
        LOG.warning("Codex stream error: %s", detail or params)

    async def _event_error(self, params: Dict[str, Any]) -> None:
        payload = params.get("error") or params.get("msg") or params
        await self._publish(Message(
            MessageType.ERROR.value,
            error_message(payload),
        ))

    async def _handle_dynamic_tool_call(
        self, request_id: Any, params: Dict[str, Any]
    ) -> None:
        handler = self.options.local_tool_handler
        if handler is None:
            await self._rpc.respond(request_id, {
                "contentItems": tool_result_content({
                    "error": "Local workspace tools are unavailable"
                }),
                "success": False,
            })
            return

        call_id = params.get("callId") or str(request_id)
        cache_key = "{}:{}:{}".format(
            params.get("threadId") or self.thread_id or "",
            params.get("turnId") or "",
            call_id,
        )
        cached = self._tool_call_results.get(cache_key)
        if cached is not None:
            await self._rpc.respond(request_id, cached)
            return
        existing = self._tool_call_futures.get(cache_key)
        if existing is not None:
            await self._rpc.respond(
                request_id, await asyncio.shield(existing)
            )
            return

        result_future = asyncio.get_event_loop().create_future()
        self._tool_call_futures[cache_key] = result_future
        try:
            tool_name = params.get("tool") or params.get("name") or ""
            namespace = params.get("namespace") or ""
            if not namespace and tool_name in self._dynamic_tool_aliases:
                namespace, tool_name = self._dynamic_tool_aliases[tool_name]
            if tool_name in self.options.local_tools_require_approval:
                approved = await self._approvals.ask(
                    "dynamic:" + call_id,
                    "local_workspace." + tool_name,
                    params.get("arguments") or {},
                )
                if not approved:
                    raise PermissionError("User denied local workspace write")
            value = await handler(
                namespace,
                tool_name,
                params.get("arguments") or {},
            )
            result = {
                "contentItems": tool_result_content(value),
                "success": True,
            }
        except asyncio.CancelledError:
            if not result_future.done():
                result_future.cancel()
            raise
        except Exception as exc:
            LOG.warning("Local dynamic tool failed: %s", exc)
            result = {
                "contentItems": tool_result_content({
                    "error": str(exc),
                    "type": type(exc).__name__,
                }),
                "success": False,
            }
        finally:
            self._tool_call_futures.pop(cache_key, None)
        self._tool_call_results[cache_key] = result
        self._tool_call_results.move_to_end(cache_key)
        while len(self._tool_call_results) > self._tool_result_cache_limit:
            self._tool_call_results.popitem(last=False)
        if not result_future.done():
            result_future.set_result(result)
        await self._rpc.respond(request_id, result)

    async def _handle_item_started(self, params: Dict[str, Any]) -> None:
        item = params.get("item", {})
        item_id = item.get("id")
        if item_id:
            self._item_cache[item_id] = item
        converter = _STARTED_ITEM_CONVERTERS.get(item.get("type"))
        if converter is not None:
            await self._publish(converter(item))

    async def _handle_item_completed(self, params: Dict[str, Any]) -> None:
        item = params.get("item", {})
        converter = _COMPLETED_ITEM_CONVERTERS.get(item.get("type"))
        message = converter(item) if converter is not None else None
        if message is not None:
            await self._publish(message)

    async def send_approval_response(self, approval_id: str, response_data: Dict[str, Any]) -> None:
        self._approvals.resolve(approval_id, response_data)

    async def _finish_approval(self, request_id, tool_name, arguments):
        outcome = await self._approvals.ask(
            str(request_id), tool_name, arguments
        )
        await self._rpc.respond(
            request_id, _decision(outcome)
        )

    async def _handle_command_approval(self, request_id: Any, params: Dict[str, Any]) -> None:
        """Handle a command execution approval request."""
        LOG.info(
            "Command approval request [rpc_id=%s, command_chars=%d, cwd_set=%s]",
            request_id,
            len(params.get("command", "")),
            bool(params.get("cwd")),
        )
        await self._finish_approval(
            request_id,
            "command_execution",
            {"command": params.get("command", "")},
        )

    async def _handle_file_approval(self, request_id: Any, params: Dict[str, Any]) -> None:
        """Handle a file change approval request."""
        LOG.info("File approval request [rpc_id=%s]", request_id)
        item = self._item_cache.get(params.get("itemId")) or {}
        arguments = dict(params)
        preview = self._generate_file_change_diff(item)
        arguments.update(
            {"processed_diff": preview} if preview else {}
        )
        await self._finish_approval(request_id, "fileChange", arguments)

    async def _handle_request_user_input(self, request_id: Any, params: Dict[str, Any]) -> None:
        """Handle request for user input via tool/requestUserInput."""
        LOG.info("User input request [rpc_id=%s]", request_id)
        questions = list(params.get("questions") or ())
        blocked = "AskUserQuestion" in set(
            self.options.disallowed_tools or ()
        )
        if blocked:
            LOG.info("Interactive questions disabled [rpc_id=%s]", request_id)
            payload = _automatic_answers(questions)
        else:
            answer = await self._approvals.ask(
                str(request_id),
                "AskUserQuestion",
                {"questions": questions},
            )
            payload = _answer_payload(answer) \
                if answer not in (False, None) else {"answers": {}}
        await self._rpc.respond(request_id, payload)

    def _generate_file_change_diff(
        self, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return build_change_preview(
            params,
            self.options.cwd,
            self.options.add_dirs,
        )

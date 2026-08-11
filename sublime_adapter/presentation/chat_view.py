import logging
import sublime

from ...providers import (
    normalize_provider_model,
    ProviderConfigurationError,
    provider_identity,
    resolve_provider,
)
from ...domain.conversation.identity import session_reference
from ...workspace.local_references import normalize_local_references
from ...workspace import DEFAULT_ENABLED_TOOLS
from ..workspace_bridge import SublimeWorkspaceTools
from ...runtime.session_registry import register_session
from ...domain.conversation.session_runtime import RuntimePhase, SessionRuntime
from ...domain.conversation.permission_flow import PermissionFlow, PermissionRoute
from .rewind import ConversationFork
from ..prompt_history import PromptHistory
from .resume_presenter import ResumePresenter
from ...shared.settings import (
    ECHO_SESSION_ID,
    ECHO_WORKSPACE,
)
from .chat_processor import EchoMessageProcessor
from ...domain.conversation.checkpoints import PromptCheckpointLedger
from .chat_panel import LoadingAnimation, RewindConfirmPanel
from .artifact import FileChangesArtifact
from ...runtime.provider_worker import ProviderWorker
from ...runtime.session_store import echo_clients
from ...workspace.project_paths import (
    additional_workspace_roots,
    get_all_folders,
    get_best_dir,
)
from .ui_components import (
    ApproveMode,
    CHAT_APPROVE_MODE,
    CHAT_CONNECTION_STATE,
    CHAT_INPUT_START,
    CHAT_MODEL,
    CHAT_PLAN_MODE,
    PlanMode,
    QuestionSequence,
    get_input_start,
)
from .composer_controls import ComposerControls, PromptGlyph
from .approval_ui import ApprovalPanel
from .welcome_panel import WelcomePanel


def _blocked_tools(settings):
    blocked = list(settings.get("disallowed_tools", ()))
    if settings.get("disable_ask_user", False) and "AskUserQuestion" not in blocked:
        blocked.append("AskUserQuestion")
    return blocked



# Constants for gutter highlights
PROMPT_HIGHLIGHT_KEY = "echo_prompt_highlight"
PROMPT_HIGHLIGHT_SCOPE = "region.purplish"
PROMPT_HIGHLIGHT_ICON = "dot"
PROMPT_HIGHLIGHT_FLAGS = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.PERSISTENT


# logger by package name
LOG = logging.getLogger("echo")

CHAT_WORKSPACE = ECHO_WORKSPACE
CHAT_SESSION_ID = ECHO_SESSION_ID
PACKAGE_NAME = "echo"
PROMPT_PREFIX = "\n❯ "  # Materialized only for submitted transcript entries.


def _checkpoint_markup(enabled):
    element = "a href='rewind'" if enabled else "span"
    close = "a" if enabled else "span"
    tone = "var(--orangish)" if enabled else "var(--foreground)"
    opacity = "0.72" if enabled else "0.24"
    return (
        "<body style='margin:0'><{element} style='color:{tone};opacity:{opacity};"
        "padding:2px 9px;text-decoration:none'>↶</{close}></body>"
    ).format(element=element, tone=tone, opacity=opacity, close=close)


class ChatSession:
    """
    Manages the state and UI for a single Echo session.
    """
    def __init__(self, window, view, cwd, add_dirs=None, session_id=None):
        self.window = window
        self.chat_view = view
        self.cwd = cwd
        self.add_dirs = additional_workspace_roots(cwd, add_dirs)
        self.loading_animation = LoadingAnimation(self.chat_view)
        self.welcome_panel = WelcomePanel(self.chat_view, cwd)

        self.model_phantom = ComposerControls(
            self.chat_view,
            self.window,
            session_lookup=lambda: echo_clients.get(self.window.id()),
        )
        self.input_marker = PromptGlyph(self.chat_view)
        if self.chat_view.settings().has(CHAT_INPUT_START):
            # Reconnect case: input line already exists, pin the marker now
            self.input_marker.update()
        self.rewind_confirm_panel = RewindConfirmPanel(self.chat_view)
        self.permission_panel = ApprovalPanel(
            self.chat_view,
            self.window,
            lambda request_id, action: self.permissions.decide(
                request_id, action
            ),
        )
        self.permissions = PermissionFlow(
            self._send_approval_reply,
            self.permission_panel.clear,
            lambda: self.window.run_command("echo_chat_implement_plan"),
        )
        self.rewind = ConversationFork(self, PROMPT_PREFIX)
        self.resume_presenter = ResumePresenter(self, PROMPT_PREFIX)
        self.prompt_history = PromptHistory()
        self.available_models = []  # Will be populated from control_response
        self.checkpoints = PromptCheckpointLedger()
        # Only persist session_id after the first user message
        self.has_sent_message = bool(session_id)
        if not self.has_sent_message:
            self.welcome_panel.update()

        # End-of-turn file changes artifact (records edit diffs, renders file list)
        self.artifact = FileChangesArtifact(self.chat_view, self.window, get_input_start)

        self.runtime = SessionRuntime(self._on_runtime_phase_change)
        # Do not carry a stale failed/disconnected state across restored views.
        self.chat_view.settings().set(CHAT_CONNECTION_STATE, "connecting")

        settings = sublime.load_settings(f"{PACKAGE_NAME}.sublime-settings")
        try:
            self.provider = resolve_provider(settings)
        except ProviderConfigurationError as exc:
            self.chat_view.run_command("echo_chat_output_append", {
                "text": "\n\n⚠️ Error: {}\n\n".format(exc)
            })
            return

        self.message_processor = EchoMessageProcessor(self)

        agent_config = self._build_agent_config(settings, session_id)

        self._launch_agent(cwd, agent_config)

    @property
    def agent_thread(self):
        return self.runtime.agent

    def _on_runtime_phase_change(self, phase):
        connection = {
            RuntimePhase.CREATED: "disconnected",
            RuntimePhase.CONNECTING: "connecting",
            # The worker is alive, but the provider has not necessarily
            # completed its handshake yet. Its `ready` event is authoritative.
            RuntimePhase.ACTIVE: "connecting",
            RuntimePhase.FAILED: "failed",
            RuntimePhase.STOPPED: "disconnected",
        }[phase]
        self.chat_view.settings().set(CHAT_CONNECTION_STATE, connection)
        self.model_phantom.update()

    def _launch_agent(self, cwd, agent_config):
        bridge = self._make_workspace_bridge(cwd, agent_config)

        def construct():
            return ProviderWorker(
                cwd,
                self.message_processor.receive,
                agent_config=agent_config,
                add_dirs=self.add_dirs,
                local_tool_handler=bridge,
            )

        return self.runtime.launch(construct)

    @staticmethod
    def get_view_session_id(view):
        value = view.settings().get(CHAT_SESSION_ID)
        if isinstance(value, str):
            return None
        if not isinstance(value, dict):
            return None
        settings = sublime.load_settings(f"{PACKAGE_NAME}.sublime-settings")
        try:
            provider = resolve_provider(settings)
        except ProviderConfigurationError:
            return None
        cwd = get_best_dir(view)
        roots = [cwd] + get_all_folders(view)
        expected = session_reference(
            provider.name,
            provider_identity(provider),
            roots,
            value.get("sessionId", ""),
        )
        if (
            value.get("provider") != expected["provider"]
            or value.get("endpointIdentity") != expected["endpointIdentity"]
            or value.get("workspaceFingerprint") != expected["workspaceFingerprint"]
        ):
            return None
        return value.get("sessionId")

    def set_view_session_id(self, view, session_id):
        """Persist session_id only if the user has sent message."""
        if not self.has_sent_message:
            return
        settings = sublime.load_settings(f"{PACKAGE_NAME}.sublime-settings")
        try:
            provider = resolve_provider(settings)
        except ProviderConfigurationError:
            return
        roots = [self.cwd] + list(self.add_dirs)
        reference = session_reference(
            provider.name, provider_identity(provider), roots, session_id
        )
        view.settings().set(CHAT_SESSION_ID, reference)
        register_session(provider.name, provider_identity(provider), roots, session_id)

    def _make_workspace_bridge(self, cwd, config):
        if config.get("provider") != "codex":
            return None
        local_config = config.get("local_tools", {})
        return SublimeWorkspaceTools(
            self.window,
            [cwd] + list(self.add_dirs),
            local_config.get(
                "enabled",
                DEFAULT_ENABLED_TOOLS,
            ),
            denied_globs=local_config.get("denied_globs"),
            max_read_bytes=local_config.get(
                "max_read_bytes", 1024 * 1024
            ),
            max_output_bytes=local_config.get(
                "max_output_bytes", 1024 * 1024
            ),
            on_file_change=self.capture_file_change,
        )

    def _build_agent_config(self, settings, session_id, plan_mode=None):
        provider = resolve_provider(settings)
        model = normalize_provider_model(
            self.window.settings().get(CHAT_MODEL)
        )
        if plan_mode is None:
            plan_mode = self.plan_mode
        provider_config = provider.config
        app_server = provider_config.get("app_server", {})
        return {
            "provider": provider.name,
            "model": model,
            "plan_mode": plan_mode == PlanMode.PLANNING,
            "disallowed_tools": _blocked_tools(settings),
            "session_id": session_id,
            "env": settings.get("env", {}),
            "app_server": app_server,
            "local_tools": settings.get("local_tools", {}),
            "cli_path": provider_config.get("cli_path"),
        }

    def request_approval(self, request_id, tool_name, input_data):
        """Route a provider request to automatic policy or interactive UI."""
        if not self.provider.capabilities.approvals:
            LOG.warning(
                "Ignoring unsupported approval request from provider %s",
                self.provider.name,
            )
            return
        approve_mode = self.window.settings().get(
            CHAT_APPROVE_MODE, ApproveMode.ALLOW_EDIT.value
        )
        package_settings = sublime.load_settings(
            f"{PACKAGE_NAME}.sublime-settings"
        )
        route = self.permissions.open(
            request_id,
            tool_name,
            input_data,
            approve_mode,
            local_policy=package_settings.get("local_tools", {}),
        )
        if route is PermissionRoute.SILENT_ALLOW:
            return
        if route is PermissionRoute.QUESTION:
            QuestionSequence(
                self.window,
                request_id,
                input_data,
                self.permissions.answer,
                self.permissions.cancel_question,
            ).run()
            return
        self.permission_panel.show(
            request_id, tool_name, input_data, approve_mode=approve_mode
        )

    def _send_approval_reply(self, request_id, response_data):
        if self.agent_thread:
            self.agent_thread.reply_approval(request_id, response_data)

    def _render_activity(self, active, text=None):
        if active:
            self.loading_animation.start(self._activity_anchor, text)
        else:
            self.loading_animation.stop()
        self.model_phantom.set_running(active)

    def begin_activity(self, text=None):
        sublime.set_timeout(
            lambda: self._render_activity(True, text), 0
        )

    def end_activity(self):
        sublime.set_timeout(
            lambda: self._render_activity(False), 0
        )

    def _activity_anchor(self):
        input_start = get_input_start(self.chat_view)
        return sublime.Region(max(0, input_start - 1), input_start)

    def stop(self):
        self._render_activity(False)
        for component in (
            self.model_phantom,
            self.input_marker,
            self.rewind_confirm_panel,
            self.welcome_panel,
        ):
            component.clear()
        self.permission_panel.clear_all()
        self.runtime.shutdown()

    def offer_plan_execution(self, plan_text="", tool_name="CodexImplementPlan"):
        request = "plan-execution-{}".format(id(self))
        context = {"plan": plan_text}
        self.permissions.stage(request, tool_name, context)
        self.permission_panel.show(request, tool_name, context)

    @property
    def plan_mode(self):
        stored = self.window.settings().get(
            CHAT_PLAN_MODE, PlanMode.FAST.value
        )
        return next(
            (mode for mode in PlanMode if mode.value == stored),
            PlanMode.FAST,
        )

    @plan_mode.setter
    def plan_mode(self, value):
        stored = value.value if isinstance(value, PlanMode) else value
        self.window.settings().set(CHAT_PLAN_MODE, stored)

    def send_input(self, user_input, region=None):
        self.rewind_confirm_panel.clear()
        agent = self.agent_thread
        if agent is None:
            provider = getattr(self, "provider", None)
            label = getattr(provider, "name", "Agent")
            self.chat_view.run_command("echo_chat_output_append", {
                "text": "\n\n⚠️ Error: {} is unavailable.\n\n".format(label)
            })
            self.end_activity()
            return
        if region is not None:
            self.add_prompt_highlight(region)
        self.message_processor.reset_plan()
        self.mark_conversation_started()
        if agent.session_id:
            self.set_view_session_id(self.chat_view, agent.session_id)
        roots = [self.cwd] + list(self.add_dirs)
        outgoing = normalize_local_references(user_input, roots)
        if not agent.enqueue(outgoing):
            self.chat_view.run_command("echo_chat_output_append", {
                "text": "\n\n⚠️ Error: Agent connection is no longer active. "
                        "Restart or reconnect the chat session.\n\n"
            })
            self.end_activity()

    def request_steering(self, text, proceed_plan=False):
        worker = self.agent_thread
        if worker is not None:
            worker.steer(text, proceed_plan=proceed_plan)

    def implement_plan(self):
        """Record the plan transition as a prompt, then ask the provider to run it."""
        insertion = get_input_start(self.chat_view, 0)
        self.chat_view.run_command(
            "echo_chat_output_append", {"text": "\nimplement the plan\n\n"}
        )
        self.add_prompt_highlight(sublime.Region(insertion, insertion))
        self.request_steering("Implement the plan.", proceed_plan=True)

    def capture_file_change(self, abs_path, rel_path, diff_text):
        worker = self.agent_thread
        environment = worker.agent_config.get("env") if worker else None
        self.artifact.record(
            abs_path, rel_path, diff_text, extra_env=environment
        )

    def present_file_changes(self):
        self.artifact.show()

    def open_change_at(self, point):
        return self.artifact.open_diff_at(point)

    def add_prompt_highlight(self, region):
        """Record a submitted prompt and render its disabled checkpoint button."""
        phantom = sublime.PhantomSet(
            self.chat_view, "echo_rewind_btn_{}".format(len(self.checkpoints))
        )
        checkpoint_index = self.checkpoints.add(region, phantom)
        self._redraw_prompt_highlights()
        self._draw_prompt_button(checkpoint_index, active=False)

    @property
    def prompt_regions(self):
        """Read-only compatibility shape used by gutter click handling."""
        return self.checkpoints.snapshot()

    def update_last_prompt_uuid(self, uuid):
        """Activate the most recent checkpoint once the server supplies its id."""
        checkpoint_index = self.checkpoints.attach_to_latest(uuid)
        if checkpoint_index is not None:
            self._draw_prompt_button(checkpoint_index, active=True)

    def _draw_prompt_button(self, checkpoint_index, active):
        """Render a checkpoint action beside its prompt."""
        checkpoint = self.checkpoints.at(checkpoint_index)
        if checkpoint.phantom is None:
            return
        end_point = self.chat_view.line(
            max(checkpoint.region.begin(), checkpoint.region.end() - 1)
        ).end()
        anchor = sublime.Region(end_point, end_point)
        checkpoint.phantom.update([sublime.Phantom(
            anchor,
            _checkpoint_markup(active),
            sublime.LAYOUT_INLINE,
            self._rewind_navigation(checkpoint_index) if active else None,
        )])

    def _rewind_navigation(self, checkpoint_index):
        def navigate(href):
            if href != "rewind":
                return
            if self.rewind_confirm_panel.visible:
                self.rewind_confirm_panel.clear()
                return
            checkpoint = self.checkpoints.at(checkpoint_index)
            self.rewind_confirm_panel.show(
                checkpoint.region,
                lambda: self._confirm_rewind(checkpoint_index),
            )
        return navigate

    def _confirm_rewind(self, checkpoint_index):
        sublime.status_message(
            "Rewinding to prompt {}...".format(checkpoint_index + 1)
        )
        self.rewind.request(checkpoint_index)

    def _redraw_prompt_highlights(self):
        """Synchronize gutter dots with the checkpoint ledger."""
        self.chat_view.add_regions(
            PROMPT_HIGHLIGHT_KEY,
            self.checkpoints.regions(),
            PROMPT_HIGHLIGHT_SCOPE,
            PROMPT_HIGHLIGHT_ICON,
            PROMPT_HIGHLIGHT_FLAGS
        )

    def clear_prompt_highlights(self):
        """Clear all prompt gutter highlights and inline buttons."""
        self.checkpoints.clear()
        self.chat_view.erase_regions(PROMPT_HIGHLIGHT_KEY)

    def _clear_conversation_state(self):
        self.end_activity()
        self.clear_prompt_highlights()
        self.artifact.clear()
        self.permissions.reset()
        self.has_sent_message = False
        self.welcome_panel.update()
        self.chat_view.settings().set(CHAT_SESSION_ID, None)

    def mark_conversation_started(self):
        if not self.has_sent_message:
            self.welcome_panel.clear()
        self.has_sent_message = True

    def _write_reset_banner(self):
        lines = ["\n\n{} session reset...\n".format(PACKAGE_NAME)]
        cwd = get_best_dir(self.chat_view)
        if cwd:
            lines.append("cwd: {}\n\n".format(cwd))
        self.chat_view.run_command(
            "echo_chat_output_append", {"text": "".join(lines)}
        )

    def reset_conversation(self):
        if self.agent_thread is None:
            return
        self._clear_conversation_state()
        self._write_reset_banner()
        self.restart_provider(preserve_session=False, quiet=True)

    def restart_provider(
        self, plan_mode=None, session_id_override=None, quiet=False,
        preserve_session=True,
    ):
        """Restart the current agent process, optionally with a new plan mode or resuming session."""
        self.end_activity()

        old_session_id = self.runtime.resumable_session(
            override=session_id_override,
            preserve=preserve_session,
        )
        self.runtime.shutdown(notify=False)

        settings = sublime.load_settings(f"{PACKAGE_NAME}.sublime-settings")
        # Resolve the selected provider rather than accepting any provider.
        try:
            self.provider = resolve_provider(settings)
        except ProviderConfigurationError as exc:
            self.chat_view.run_command("echo_chat_output_append", {
                "text": "\n\n⚠️ Error: {}\n\n".format(exc)
            })
            return

        if plan_mode is None:
            plan_mode = self.plan_mode

        agent_config = self._build_agent_config(
            settings, old_session_id, plan_mode=plan_mode
        )

        cwd = get_best_dir(self.chat_view)
        self._launch_agent(cwd, agent_config)
        LOG.info("Reconnected %s (resume: %s)", self.provider.name, bool(old_session_id))

        if not quiet:
            if old_session_id and self.provider.capabilities.resume:
                self.resume_presenter.schedule(
                    self.provider, old_session_id
                )
            else:
                self.chat_view.run_command("echo_chat_output_append", {
                    "text": "\n\n[reconnecting agent...]\n\n"
                })

    def apply_plan_mode(self, plan_mode):
        self.end_activity()
        worker = self.agent_thread
        if worker is not None:
            worker.reconfigure(plan_mode=plan_mode is PlanMode.PLANNING)
        LOG.info("Updated plan mode to %s", plan_mode.value)

"""Transient chat UI built on Sublime phantoms and popups."""

import html

import sublime

from .ui_components import get_input_start


def _stop_link(label):
    shortcut = "⌘+Esc" if sublime.platform() == "osx" else "Shift+Esc"
    return (
        '<a class="stop-hint" href="stop_conversation" '
        'title="Interrupt current turn ({})">{}</a>'
    ).format(shortcut, label)


class StatusHint:
    def __init__(self, text="stopping..."):
        self.visible = False
        self.stopping = False
        self.text = text

    def change_visibility(self, value):
        requested = bool(value)
        if requested == self.visible:
            return False
        self.visible, self.stopping = requested, False
        return True

    def change_stop_state(self, value, text=None):
        before = (self.stopping, self.text)
        after = (bool(value), self.text if text is None else text)
        self.stopping, self.text = after
        return after != before

    def render(self):
        if not self.visible:
            return ""
        label = "■"
        if self.stopping:
            label += " " + html.escape(str(self.text))
        return _stop_link(label)


class LoadingAnimation:
    """Generation-safe spinner; stale scheduled frames cannot repaint."""

    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    INTERVAL_MS = 100

    def __init__(self, view):
        self.view = view
        self.phantom_set = sublime.PhantomSet(view, "echo_loading")
        self.region_provider = None
        self.loading_text = None
        self.is_loading = False
        self._generation = 0
        self._frame_index = 0

    def start(self, region, text=None):
        self.region_provider, self.loading_text = region, text
        if self.is_loading:
            return
        self._generation += 1
        self._frame_index = 0
        self.is_loading = True
        self._draw(self._generation)

    def stop(self):
        self.is_loading = False
        self._generation += 1
        sublime.set_timeout(self._clear, 0)

    def _clear(self):
        self.phantom_set.update([])

    def _draw(self, generation):
        if not self.is_loading or generation != self._generation:
            return
        anchor = self.region_provider() \
            if callable(self.region_provider) else self.region_provider
        frame = self.FRAMES[self._frame_index % len(self.FRAMES)]
        detail = ""
        if self.loading_text:
            detail = (
                ' <span style="font-weight:normal;opacity:0.75">{}</span>'
            ).format(html.escape(str(self.loading_text)))
        markup = (
            '<body id="echo-loading"><span class="loading">'
            '{}{}</span></body>'
        ).format(frame, detail)
        phantom = sublime.Phantom(anchor, markup, sublime.LAYOUT_BLOCK)
        self.phantom_set.update([phantom])
        self._frame_index += 1
        sublime.set_timeout(
            lambda: self._draw(generation), self.INTERVAL_MS
        )


class ConversationActivity:
    """Keep spinner placement and composer state synchronized."""

    def __init__(self, view, spinner, controls):
        self._parts = (view, spinner, controls)

    def render(self, active, text=None):
        view, spinner, controls = self._parts
        if active:
            spinner.start(lambda: self._anchor(view), text)
        else:
            spinner.stop()
        controls.set_running(active)

    @staticmethod
    def _anchor(view):
        boundary = get_input_start(view)
        return sublime.Region(max(0, boundary - 1), boundary)


class RewindConfirmPanel:
    """Single-use confirmation popup for restoring an earlier prompt."""

    MARKUP = (
        '<body id="echo-rewind"><div class="dialog">'
        '<b>Restore this checkpoint?</b><br>'
        '<span>Later messages and recorded changes will be removed.</span><br>'
        '<a href="restore">Restore</a>&nbsp;&nbsp;'
        '<a href="dismiss">Keep current conversation</a>'
        '</div></body>'
    )

    def __init__(self, view):
        self.view = view
        self._callback = None

    @property
    def visible(self):
        return self._callback is not None

    def show(self, region, on_confirm):
        self._callback = on_confirm
        self.view.show_popup(
            self.MARKUP,
            location=region.begin(),
            flags=0,
            max_width=560,
            on_navigate=self._navigate,
            on_hide=self.clear,
        )

    def _navigate(self, action):
        callback, self._callback = self._callback, None
        self.view.hide_popup()
        if action == "restore" and callback is not None:
            callback()

    def clear(self):
        self._callback = None
        self.view.hide_popup()

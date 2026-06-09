from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Callable, Protocol, Sequence

from .installer import (
    AGENTS,
    SCOPES,
    InstallationStatus,
    Installer,
    InstallerError,
    InstallResult,
    SkillProject,
    default_repo_path,
    describe_target,
    find_repo_root,
    install_source_metadata,
    inspect_installations,
    running_on_tty,
)


class UsageError(Exception):
    pass


class BackRequested(Exception):
    pass


VERSION_CHANGE_COLORS = {
    "upgrade": "32",
    "downgrade": "31",
}


TARGET_ALL = "all"
AGENT_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
}
SCOPE_LABELS = {
    "dir": "Directory",
    "global": "User global",
}
AGENT_TARGET_VALUES = set(AGENTS)
INSTALLATION_TARGET_SEPARATOR = ":"
SPECIFIC_DIRECTORY_VALUE = "specific"
InstallationTarget = tuple[str, str, bool]
TEXTUAL_APP_TITLE = "Agent Skill Installer"
CommandPreviewBuilder = Callable[[object], str | None]
PromptValidator = Callable[[str], str | None]
PROMPT_BACK = "__agent_skill_installer_prompt_back__"
DEFAULT_SUBMIT_LABEL = "Continue"
DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE = "Complete the selections to build the no-UI command."
ACTION_BUTTON_IDS = ("continue", "back", "quit")


def focus_prompt_action(app, focused_id: str | None, button_type, offset: int) -> None:
    try:
        current_index = ACTION_BUTTON_IDS.index(focused_id or "")
    except ValueError:
        current_index = 0
    target_id = ACTION_BUTTON_IDS[(current_index + offset) % len(ACTION_BUTTON_IDS)]
    app.query_one(f"#{target_id}", button_type).focus()


class Prompter(Protocol):
    def select(
        self,
        message: str,
        choices: Sequence[dict[str, str]],
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> str:
        ...

    def checkbox(
        self,
        message: str,
        choices: Sequence[dict[str, str]],
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        default_values: Sequence[str] | None = None,
        empty_message: str = "Choose at least one target.",
        accept_highlighted_on_empty: bool = True,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> list[str]:
        ...

    def path(
        self,
        message: str,
        default: Path,
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> Path:
        ...

    def text(
        self,
        message: str,
        default: str,
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> str:
        ...

    def version(
        self,
        message: str,
        default: str,
        choices: Sequence[dict[str, str]],
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        validator: PromptValidator | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> str:
        ...


class TextualPrompter:
    def __init__(self, project: SkillProject) -> None:
        self.project = project
        try:
            import textual  # noqa: F401
        except ImportError as error:
            raise InstallerError(
                f"interactive UI requires Textual; install {project.package_name} "
                "with dependencies or run with --no-ui"
            ) from error

    def select(
        self,
        message: str,
        choices: Sequence[dict[str, str]],
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> str:
        result = run_textual_select(
            message,
            choices,
            command_preview=command_preview,
            command_preview_builder=command_preview_builder,
            summary=summary,
            summary_builder=summary_builder,
            submit_label=submit_label,
        )
        if result == PROMPT_BACK:
            raise BackRequested
        if result is None:
            raise KeyboardInterrupt
        return str(result)

    def checkbox(
        self,
        message: str,
        choices: Sequence[dict[str, str]],
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        default_values: Sequence[str] | None = None,
        empty_message: str = "Choose at least one target.",
        accept_highlighted_on_empty: bool = True,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> list[str]:
        result = run_textual_checkbox(
            message,
            choices,
            command_preview=command_preview,
            command_preview_builder=command_preview_builder,
            summary=summary,
            summary_builder=summary_builder,
            default_values=default_values,
            empty_message=empty_message,
            accept_highlighted_on_empty=accept_highlighted_on_empty,
            submit_label=submit_label,
        )
        if result == PROMPT_BACK:
            raise BackRequested
        if result is None:
            raise KeyboardInterrupt
        return [str(item) for item in result]

    def path(
        self,
        message: str,
        default: Path,
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> Path:
        result = run_textual_path(
            message,
            default,
            command_preview=command_preview,
            command_preview_builder=command_preview_builder,
            summary=summary,
            summary_builder=summary_builder,
            submit_label=submit_label,
        )
        if result == PROMPT_BACK:
            raise BackRequested
        if result is None:
            raise KeyboardInterrupt
        value = str(result).strip()
        return Path(value).expanduser() if value else default

    def text(
        self,
        message: str,
        default: str,
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> str:
        result = run_textual_text(
            message,
            default,
            command_preview=command_preview,
            command_preview_builder=command_preview_builder,
            summary=summary,
            summary_builder=summary_builder,
            submit_label=submit_label,
        )
        if result == PROMPT_BACK:
            raise BackRequested
        if result is None:
            raise KeyboardInterrupt
        value = str(result).strip()
        return value or default

    def version(
        self,
        message: str,
        default: str,
        choices: Sequence[dict[str, str]],
        *,
        command_preview: str | None = None,
        command_preview_builder: CommandPreviewBuilder | None = None,
        summary: str | None = None,
        summary_builder: CommandPreviewBuilder | None = None,
        validator: PromptValidator | None = None,
        submit_label: str = DEFAULT_SUBMIT_LABEL,
    ) -> str:
        result = run_textual_version(
            message,
            default,
            choices,
            command_preview=command_preview,
            command_preview_builder=command_preview_builder,
            summary=summary,
            summary_builder=summary_builder,
            validator=validator,
            submit_label=submit_label,
        )
        if result == PROMPT_BACK:
            raise BackRequested
        if result is None:
            raise KeyboardInterrupt
        value = str(result).strip()
        return value or default


def render_command_preview(
    command_preview: str | None,
    *,
    Button,
    Horizontal,
    Static,
    Vertical,
    force: bool = False,
    empty_message: str = "",
    preview_class: str | None = None,
):
    if not command_preview and not force:
        return
    with Vertical(
        id="command-preview",
        classes=preview_class or command_preview_classes(command_preview),
    ):
        with Horizontal(id="command-preview-header"):
            yield Static("Non-interactive command", id="command-preview-title")
            copy_button = Button(
                "Copy\nCtrl+C",
                id="copy-command",
                classes="copy-command",
                disabled=not bool(command_preview),
            )
            copy_button.can_focus = False
            yield copy_button
        yield Static(
            command_preview or empty_message,
            id="command-preview-command",
        )


def render_installation_summary(
    summary: str | None,
    *,
    title: str = "Current selection",
    Static,
    Vertical,
):
    with Vertical(id="installation-summary"):
        yield Static(title, id="installation-summary-title")
        yield Static(summary or "", id="installation-summary-content")


def command_preview_classes(command_preview: str | None) -> str:
    if not command_preview:
        return ""
    try:
        parts = shlex.split(command_preview)
    except ValueError:
        parts = command_preview.split()
    if "uninstall" in parts:
        return "uninstall-preview"
    if "install" in parts:
        return "install-preview"
    return ""


def command_preview_class_for_value(value: object) -> str:
    text = str(value)
    if text in {"install", "pypi", "wheel", "github", "local", "editable", "copy"}:
        return "install-preview"
    if text == "uninstall":
        return "uninstall-preview"
    return ""


def command_preview_class_for_summary(summary: str | None) -> str:
    text = (summary or "").strip().lower()
    if text.startswith("installing "):
        return "install-preview"
    if text.startswith("uninstalling ") or text.startswith("removing "):
        return "uninstall-preview"
    return ""


def effective_command_preview_class(
    command_preview: str | None,
    value: object | None = None,
    summary: str | None = None,
) -> str:
    return (
        command_preview_classes(command_preview)
        or (
            command_preview_class_for_value(value)
            if value is not None
            else ""
        )
        or command_preview_class_for_summary(summary)
    )


def update_command_preview_display(
    app,
    command_preview: str | None,
    Static,
    *,
    empty_message: str = "Choose at least one target.",
    preview_class: str | None = None,
) -> None:
    panels = list(app.query("#command-preview"))
    if not panels:
        return
    app.query_one("#command-preview-command", Static).update(
        command_preview or empty_message
    )
    for copy_button in app.query("#copy-command"):
        copy_button.disabled = not bool(command_preview)
    panel = panels[0]
    existing_class = ""
    for class_name in ("install-preview", "uninstall-preview"):
        if (
            panel.has_class(class_name)
            if hasattr(panel, "has_class")
            else class_name in getattr(panel, "classes", set())
        ):
            existing_class = class_name
            break
    for class_name in ("install-preview", "uninstall-preview"):
        panel.remove_class(class_name)
    active_class = (
        preview_class
        or command_preview_classes(command_preview or "")
        or existing_class
    )
    if active_class:
        panel.add_class(active_class)


def update_installation_summary_display(app, summary: str | None, Static) -> None:
    panels = list(app.query("#installation-summary"))
    if not panels:
        return
    panel = panels[0]
    panel.display = bool(summary)
    app.query_one("#installation-summary-content", Static).update(summary or "")


def copy_command_to_clipboard(app, command_preview: str | None) -> None:
    if not command_preview:
        return
    app.copy_to_clipboard(command_preview)
    app.notify("Copied no-UI command. Ctrl+C again to exit.")


def handle_ctrl_c(app, command_preview: str | None) -> None:
    if command_preview and not getattr(app, "ctrl_c_copied", False):
        copy_command_to_clipboard(app, command_preview)
        app.ctrl_c_copied = True
        return
    app.exit(None)


def run_textual_select(
    message: str,
    choices: Sequence[dict[str, str]],
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
) -> str | None:
    return make_textual_select_app(
        message,
        choices,
        command_preview=command_preview,
        command_preview_builder=command_preview_builder,
        summary=summary,
        summary_builder=summary_builder,
        submit_label=submit_label,
    ).run()


def make_textual_select_app(
    message: str,
    choices: Sequence[dict[str, str]],
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
):
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, RadioButton, RadioSet, Static

    values = [choice["value"] for choice in choices]
    choice_details = [str(choice.get("description", "")) for choice in choices]
    has_choice_details = any(choice_details)
    initial_command_preview = (
        command_preview_builder(values[0])
        if command_preview_builder is not None and values
        else command_preview
    )
    initial_summary = (
        summary_builder(values[0])
        if summary_builder is not None and values
        else summary
    )
    initial_preview_class = effective_command_preview_class(
        initial_command_preview,
        values[0] if command_preview_builder is not None and values else None,
        initial_summary,
    )
    has_summary_panel = summary is not None or summary_builder is not None

    class PromptRadioSet(RadioSet):
        BINDINGS = [
            ("left,up", "previous_button", "Previous option"),
            ("right,down", "next_button", "Next option"),
            Binding("space", "select_current_option", "Select", key_display="Space"),
            ("enter", "accept_current_option", "Accept"),
        ]

        def current_option_index(self) -> int:
            selected_index = getattr(self, "_selected", -1)
            return selected_index if isinstance(selected_index, int) else -1

        def sync_active_option(self) -> None:
            selected_index = self.current_option_index()
            if selected_index >= 0:
                self.app.update_active_choice(selected_index)

        def press_current_option(self) -> None:
            selected_index = self.current_option_index()
            if selected_index < 0:
                return
            self._nodes[selected_index].value = True
            self.app.update_active_choice(selected_index)

        def on_key(self, event: events.Key) -> None:
            if event.key == "space":
                event.prevent_default()
                event.stop()
                self.action_select_current_option()
            elif event.key == "enter":
                event.prevent_default()
                event.stop()
                self.action_accept_current_option()

        def action_next_button(self) -> None:
            if self.current_option_index() >= len(values) - 1:
                self.app.action_focus_actions()
                return
            super().action_next_button()
            self.sync_active_option()

        def focus_last_option(self) -> None:
            while self.current_option_index() < len(values) - 1:
                super().action_next_button()
            self.sync_active_option()

        def focus_first_option(self) -> None:
            while self.current_option_index() > 0:
                super().action_previous_button()
            self.sync_active_option()

        def action_previous_button(self) -> None:
            if self.current_option_index() <= 0:
                return
            super().action_previous_button()
            self.sync_active_option()

        def action_select_current_option(self) -> None:
            self.press_current_option()

        def action_accept_current_option(self) -> None:
            self.press_current_option()
            self.app.action_accept_options()

    class CopyButton(Button):
        BINDINGS = [
            ("down", "focus_first_option", "Options"),
        ]

        def action_focus_first_option(self) -> None:
            self.app.action_focus_first_option()

    class PromptButton(Button):
        BINDINGS = [
            ("left", "focus_previous_action", "Previous action"),
            ("right", "focus_next_action", "Next action"),
            ("up", "focus_options", "Options"),
        ]

        def action_focus_previous_action(self) -> None:
            self.app.action_focus_previous_action()

        def action_focus_next_action(self) -> None:
            self.app.action_focus_next_action()

        def action_focus_options(self) -> None:
            self.app.action_focus_options()

    class RadioPromptApp(App[str | None]):
        CSS = PROMPT_CSS
        BINDINGS = [
            ("down", "focus_actions", "Actions"),
            Binding("escape", "back", "Back", key_display="ESC"),
            ("ctrl+c", "copy_or_cancel", "Copy"),
            Binding("ctrl+q", "quit_prompt", "Quit", key_display="Ctrl+Q"),
        ]
        current_command_preview = initial_command_preview
        ctrl_c_copied = False

        def compose(self) -> ComposeResult:
            self.title = TEXTUAL_APP_TITLE
            yield Header()
            if has_summary_panel:
                yield from render_installation_summary(
                    initial_summary,
                    Static=Static,
                    Vertical=Vertical,
                )
            yield from render_command_preview(
                self.current_command_preview,
                Button=CopyButton,
                Horizontal=Horizontal,
                Static=Static,
                Vertical=Vertical,
                force=command_preview_builder is not None,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=initial_preview_class,
            )
            with Vertical(id="dialog"):
                yield Static(message, id="message")
                with PromptRadioSet(id="choice"):
                    for index, choice in enumerate(choices):
                        yield RadioButton(choice["name"], value=index == 0)
                if has_choice_details:
                    yield Static(choice_details[0], id="choice-details")
                with Horizontal(id="actions"):
                    yield PromptButton(submit_label, id="continue", variant="primary")
                    yield PromptButton("Back (ESC)", id="back")
                    yield PromptButton("Quit Ctrl+Q", id="quit")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#choice", PromptRadioSet).focus()
            if has_summary_panel:
                update_installation_summary_display(self, initial_summary, Static)

        def focused_id(self) -> str | None:
            return getattr(self.screen.focused, "id", None)

        def action_quit_prompt(self) -> None:
            self.exit(None)

        def action_back(self) -> None:
            self.exit(PROMPT_BACK)

        def action_copy_or_cancel(self) -> None:
            handle_ctrl_c(self, self.current_command_preview)

        def action_focus_actions(self) -> None:
            self.query_one("#continue", PromptButton).focus()

        def action_focus_copy_command(self) -> None:
            return

        def action_focus_first_option(self) -> None:
            choice = self.query_one("#choice", PromptRadioSet)
            choice.focus_first_option()
            choice.focus()

        def action_focus_options(self) -> None:
            choice = self.query_one("#choice", PromptRadioSet)
            choice.focus_last_option()
            choice.focus()

        def approved_choice_index(self) -> int:
            choice = self.query_one("#choice", PromptRadioSet)
            current_index = choice.current_option_index()
            if current_index >= 0:
                choice.press_current_option()
                return current_index
            return choice.pressed_index

        def action_accept_options(self) -> None:
            selected_index = self.approved_choice_index()
            self.exit(str(values[selected_index or 0]))

        def action_focus_previous_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, -1)

        def action_focus_next_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, 1)

        def update_command_preview(self, value: str) -> None:
            if command_preview_builder is None:
                return
            self.current_command_preview = command_preview_builder(value)
            update_command_preview_display(
                self,
                self.current_command_preview,
                Static,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=effective_command_preview_class(
                    self.current_command_preview,
                    value,
                    summary_builder(value) if summary_builder is not None else summary,
                ),
            )

        def update_installation_summary(self, value: str) -> None:
            if summary_builder is None:
                return
            update_installation_summary_display(self, summary_builder(value), Static)

        def update_choice_details(self, selected_index: int) -> None:
            if not has_choice_details:
                return
            detail = choice_details[selected_index] if selected_index >= 0 else ""
            self.query_one("#choice-details", Static).update(detail)

        def update_active_choice(self, selected_index: int) -> None:
            if selected_index < 0:
                return
            value = str(values[selected_index])
            self.update_command_preview(value)
            self.update_installation_summary(value)
            self.update_choice_details(selected_index)

        def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
            selected_index = event.radio_set.pressed_index
            self.update_active_choice(selected_index)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "copy-command":
                copy_command_to_clipboard(self, self.current_command_preview)
                return
            if event.button.id == "back":
                self.exit(PROMPT_BACK)
                return
            if event.button.id == "quit":
                self.exit(None)
                return
            if event.button.id == "continue":
                selected_index = self.approved_choice_index()
                self.exit(str(values[selected_index or 0]))

    return RadioPromptApp()


def run_textual_checkbox(
    message: str,
    choices: Sequence[dict[str, str]],
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    default_values: Sequence[str] | None = None,
    empty_message: str = "Choose at least one target.",
    accept_highlighted_on_empty: bool = True,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
) -> list[str] | None:
    return make_textual_checkbox_app(
        message,
        choices,
        command_preview=command_preview,
        command_preview_builder=command_preview_builder,
        summary=summary,
        summary_builder=summary_builder,
        default_values=default_values,
        empty_message=empty_message,
        accept_highlighted_on_empty=accept_highlighted_on_empty,
        submit_label=submit_label,
    ).run()


def make_textual_checkbox_app(
    message: str,
    choices: Sequence[dict[str, str]],
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    default_values: Sequence[str] | None = None,
    empty_message: str = "Choose at least one target.",
    accept_highlighted_on_empty: bool = True,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
):
    from rich.segment import Segment
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, SelectionList, Static
    from textual.widgets.option_list import OptionDoesNotExist
    from textual.widgets.selection_list import Selection
    from textual.strip import Strip

    selections = [
        Selection(
            choice["name"],
            choice["value"],
            id=choice["value"],
            disabled=bool(choice.get("disabled", False)),
        )
        for choice in choices
    ]
    all_control_values = [choice["value"] for choice in choices]
    grouped_target_values = [
        choice["value"]
        for choice in choices
        if choice["value"] != TARGET_ALL
        and choice.get("kind", "target") == "target"
        and not bool(choice.get("disabled", False))
    ]
    initial_selected_values = [
        value
        for value in (default_values or [])
        if value in all_control_values
    ]
    has_all_control = TARGET_ALL in all_control_values
    initial_command_preview = (
        command_preview_builder(initial_selected_values)
        if command_preview_builder is not None
        else command_preview
    )
    initial_summary = (
        summary_builder(initial_selected_values)
        if summary_builder is not None
        else summary
    )
    initial_preview_class = effective_command_preview_class(
        initial_command_preview,
        summary=initial_summary,
    )
    has_summary_panel = summary is not None or summary_builder is not None

    class PromptSelectionList(SelectionList[str]):
        BINDINGS = [
            Binding("space", "toggle_highlighted", "Select", key_display="Space"),
            Binding("enter", "accept_highlighted", "Accept", key_display="Enter"),
        ]

        def action_toggle_highlighted(self) -> None:
            if self.highlighted is None:
                return
            selection = self.get_option_at_index(self.highlighted)
            if not selection.disabled:
                self.toggle(selection)

        def action_accept_highlighted(self) -> None:
            if not self.selected and accept_highlighted_on_empty:
                self.action_toggle_highlighted()
            self.app.action_accept_selected_targets()

        def render_line(self, y: int):
            line = super().render_line(y)
            _, scroll_y = self.scroll_offset
            selection_index = scroll_y + y
            try:
                selection = self.get_option_at_index(selection_index)
            except OptionDoesNotExist:
                return line
            if not selection.disabled or selection.value not in self.selected:
                return line

            segments = list(line)
            if len(segments) < 4:
                return line
            disabled_style = segments[3].style or self.rich_style
            for index in range(3):
                segment = segments[index]
                segments[index] = Segment(
                    segment.text,
                    disabled_style,
                    segment.control,
                )
            return Strip(segments, line.cell_length)

    class PromptButton(Button):
        BINDINGS = [
            ("left", "focus_previous_action", "Previous action"),
            ("right", "focus_next_action", "Next action"),
            ("up", "focus_options", "Options"),
        ]

        def action_focus_previous_action(self) -> None:
            self.app.action_focus_previous_action()

        def action_focus_next_action(self) -> None:
            self.app.action_focus_next_action()

        def action_focus_options(self) -> None:
            self.app.action_focus_options()

    class CheckboxPromptApp(App[list[str] | None]):
        CSS = PROMPT_CSS
        BINDINGS = [
            Binding("escape", "back", "Back", key_display="ESC"),
            ("ctrl+c", "copy_or_cancel", "Copy"),
            Binding("ctrl+q", "quit_prompt", "Quit", key_display="Ctrl+Q"),
        ]
        all_mode = False
        syncing_all = False
        previous_target_selection: set[str] = set()
        current_command_preview = initial_command_preview
        ctrl_c_copied = False

        def compose(self) -> ComposeResult:
            self.title = TEXTUAL_APP_TITLE
            yield Header()
            if has_summary_panel:
                yield from render_installation_summary(
                    initial_summary,
                    Static=Static,
                    Vertical=Vertical,
                )
            yield from render_command_preview(
                self.current_command_preview,
                Button=Button,
                Horizontal=Horizontal,
                Static=Static,
                Vertical=Vertical,
                force=command_preview_builder is not None,
                empty_message=empty_message,
                preview_class=initial_preview_class,
            )
            with Vertical(id="dialog"):
                yield Static(message, id="message")
                yield PromptSelectionList(*selections, id="choices")
                yield Static("", id="error")
                with Horizontal(id="actions"):
                    yield PromptButton(submit_label, id="continue", variant="primary")
                    yield PromptButton("Back (ESC)", id="back")
                    yield PromptButton("Quit Ctrl+Q", id="quit")
            yield Footer()

        def on_mount(self) -> None:
            choices_list = self.query_one("#choices", PromptSelectionList)
            if has_all_control and TARGET_ALL in initial_selected_values:
                self.enable_all_mode(choices_list)
                if has_summary_panel:
                    update_installation_summary_display(self, initial_summary, Static)
                choices_list.focus()
                return
            self.syncing_all = True
            try:
                for value in initial_selected_values:
                    choices_list.select(value)
            finally:
                self.syncing_all = False
            choices_list.focus()
            if has_summary_panel:
                update_installation_summary_display(self, initial_summary, Static)

        def focused_id(self) -> str | None:
            return getattr(self.screen.focused, "id", None)

        def action_quit_prompt(self) -> None:
            self.exit(None)

        def action_back(self) -> None:
            self.exit(PROMPT_BACK)

        def action_copy_or_cancel(self) -> None:
            handle_ctrl_c(self, self.current_command_preview)

        def action_focus_options(self) -> None:
            self.query_one("#choices", PromptSelectionList).focus()

        def action_focus_previous_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, -1)

        def action_focus_next_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, 1)

        def selected_values(self, choices_list: PromptSelectionList) -> list[str]:
            return [str(item) for item in choices_list.selected]

        def update_command_preview(
            self,
            choices_list: PromptSelectionList,
        ) -> None:
            if command_preview_builder is None:
                return
            self.current_command_preview = command_preview_builder(
                self.selected_values(choices_list)
            )
            selected = self.selected_values(choices_list)
            update_command_preview_display(
                self,
                self.current_command_preview,
                Static,
                preview_class=effective_command_preview_class(
                    self.current_command_preview,
                    summary=(
                        summary_builder(selected)
                        if summary_builder is not None
                        else summary
                    ),
                ),
            )

        def update_installation_summary(
            self,
            choices_list: PromptSelectionList,
        ) -> None:
            if summary_builder is None:
                return
            update_installation_summary_display(
                self,
                summary_builder(self.selected_values(choices_list)),
                Static,
            )

        def on_selection_list_selection_toggled(
            self,
            event: SelectionList.SelectionToggled[str],
        ) -> None:
            if self.syncing_all or not has_all_control:
                return

            choices_list = self.query_one("#choices", PromptSelectionList)
            value = str(event.selection.value)
            selected = {str(item) for item in choices_list.selected}
            if value == TARGET_ALL:
                if TARGET_ALL in selected:
                    self.previous_target_selection = {
                        item
                        for item in selected
                        if item != TARGET_ALL
                    }
                    self.enable_all_mode(choices_list)
                else:
                    self.disable_all_mode(choices_list)
                return

            explicit_selection = {
                item
                for item in selected
                if item != TARGET_ALL
            }
            if TARGET_ALL in selected:
                self.disable_all_mode(
                    choices_list,
                    restored_values=explicit_selection,
                )
                return

            self.previous_target_selection = explicit_selection

        def on_selection_list_selected_changed(
            self,
            event: SelectionList.SelectedChanged[str],
        ) -> None:
            self.update_command_preview(event.selection_list)
            self.update_installation_summary(event.selection_list)

        def enable_all_mode(self, choices_list: PromptSelectionList) -> None:
            self.all_mode = True
            self.syncing_all = True
            try:
                choices_list.select(TARGET_ALL)
                for value in grouped_target_values:
                    choices_list.enable_option(value)
                    choices_list.deselect(value)
            finally:
                self.syncing_all = False

        def disable_all_mode(
            self,
            choices_list: PromptSelectionList,
            restored_values: set[str] | None = None,
        ) -> None:
            self.all_mode = False
            restored = (
                set(self.previous_target_selection)
                if restored_values is None
                else set(restored_values)
            )
            self.syncing_all = True
            try:
                for value in grouped_target_values:
                    choices_list.enable_option(value)
                choices_list.deselect_all()
                for value in grouped_target_values:
                    if value in restored:
                        choices_list.select(value)
            finally:
                self.syncing_all = False

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "copy-command":
                if self.current_command_preview is None:
                    self.query_one("#error", Static).update(empty_message)
                    return
                copy_command_to_clipboard(self, self.current_command_preview)
                return
            if event.button.id == "back":
                self.exit(PROMPT_BACK)
                return
            if event.button.id == "quit":
                self.exit(None)
                return
            if event.button.id != "continue":
                return
            self.action_accept_selected_targets()

        def action_accept_selected_targets(self) -> None:
            selected = list(self.query_one("#choices", PromptSelectionList).selected)
            if not selected:
                self.query_one("#error", Static).update(empty_message)
                return
            self.exit([str(value) for value in selected])

    return CheckboxPromptApp()


def run_textual_path(
    message: str,
    default: Path,
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
) -> str | None:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, Input, Static

    initial_command_preview = (
        command_preview_builder(default)
        if command_preview_builder is not None
        else command_preview
    )
    initial_summary = (
        summary_builder(default)
        if summary_builder is not None
        else summary
    )
    initial_preview_class = effective_command_preview_class(
        initial_command_preview,
        summary=initial_summary,
    )
    has_summary_panel = summary is not None or summary_builder is not None

    class PromptInput(Input):
        BINDINGS = [
            ("down", "focus_actions", "Actions"),
        ]

        def action_focus_actions(self) -> None:
            self.app.action_focus_actions()

    class PromptButton(Button):
        BINDINGS = [
            ("left", "focus_previous_action", "Previous action"),
            ("right", "focus_next_action", "Next action"),
            ("up", "focus_options", "Options"),
        ]

        def action_focus_previous_action(self) -> None:
            self.app.action_focus_previous_action()

        def action_focus_next_action(self) -> None:
            self.app.action_focus_next_action()

        def action_focus_options(self) -> None:
            self.app.action_focus_options()

    class PathPromptApp(App[str | None]):
        CSS = PROMPT_CSS
        BINDINGS = [
            Binding("escape", "back", "Back", key_display="ESC"),
            ("ctrl+c", "copy_or_cancel", "Copy"),
            Binding("ctrl+q", "quit_prompt", "Quit", key_display="Ctrl+Q"),
        ]
        current_command_preview = initial_command_preview
        ctrl_c_copied = False

        def compose(self) -> ComposeResult:
            self.title = TEXTUAL_APP_TITLE
            yield Header()
            if has_summary_panel:
                yield from render_installation_summary(
                    initial_summary,
                    Static=Static,
                    Vertical=Vertical,
                )
            yield from render_command_preview(
                self.current_command_preview,
                Button=Button,
                Horizontal=Horizontal,
                Static=Static,
                Vertical=Vertical,
                force=command_preview_builder is not None,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=initial_preview_class,
            )
            with Vertical(id="dialog"):
                yield Static(message, id="message")
                yield PromptInput(value=str(default), id="path")
                with Horizontal(id="actions"):
                    yield PromptButton(submit_label, id="continue", variant="primary")
                    yield PromptButton("Back (ESC)", id="back")
                    yield PromptButton("Quit Ctrl+Q", id="quit")
            yield Footer()

        def on_mount(self) -> None:
            if has_summary_panel:
                update_installation_summary_display(self, initial_summary, Static)

        def focused_id(self) -> str | None:
            return getattr(self.screen.focused, "id", None)

        def action_quit_prompt(self) -> None:
            self.exit(None)

        def action_back(self) -> None:
            self.exit(PROMPT_BACK)

        def action_copy_or_cancel(self) -> None:
            handle_ctrl_c(self, self.current_command_preview)

        def action_focus_actions(self) -> None:
            self.query_one("#continue", PromptButton).focus()

        def action_focus_options(self) -> None:
            self.query_one("#path", PromptInput).focus()

        def action_focus_previous_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, -1)

        def action_focus_next_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, 1)

        def update_command_preview(self, value: str) -> None:
            if command_preview_builder is None:
                return
            path = Path(value).expanduser() if value.strip() else default
            self.current_command_preview = command_preview_builder(path)
            current_summary = (
                summary_builder(path)
                if summary_builder is not None
                else summary
            )
            update_command_preview_display(
                self,
                self.current_command_preview,
                Static,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=effective_command_preview_class(
                    self.current_command_preview,
                    summary=current_summary,
                ),
            )

        def update_installation_summary(self, value: str) -> None:
            if summary_builder is None:
                return
            path = Path(value).expanduser() if value.strip() else default
            update_installation_summary_display(
                self,
                summary_builder(path),
                Static,
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            self.update_command_preview(event.value)
            self.update_installation_summary(event.value)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            self.exit(event.value.strip() or str(default))

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "copy-command":
                copy_command_to_clipboard(self, self.current_command_preview)
                return
            if event.button.id == "back":
                self.exit(PROMPT_BACK)
                return
            if event.button.id == "quit":
                self.exit(None)
                return
            if event.button.id == "continue":
                value = self.query_one("#path", PromptInput).value.strip()
                self.exit(value or str(default))

    return PathPromptApp().run()


def run_textual_text(
    message: str,
    default: str,
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
) -> str | None:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, Input, Static

    initial_command_preview = (
        command_preview_builder(default)
        if command_preview_builder is not None
        else command_preview
    )
    initial_summary = (
        summary_builder(default)
        if summary_builder is not None
        else summary
    )
    initial_preview_class = effective_command_preview_class(
        initial_command_preview,
        summary=initial_summary,
    )
    has_summary_panel = summary is not None or summary_builder is not None

    class PromptInput(Input):
        BINDINGS = [
            ("down", "focus_actions", "Actions"),
        ]

        def action_focus_actions(self) -> None:
            self.app.action_focus_actions()

    class PromptButton(Button):
        BINDINGS = [
            ("left", "focus_previous_action", "Previous action"),
            ("right", "focus_next_action", "Next action"),
            ("up", "focus_options", "Options"),
        ]

        def action_focus_previous_action(self) -> None:
            self.app.action_focus_previous_action()

        def action_focus_next_action(self) -> None:
            self.app.action_focus_next_action()

        def action_focus_options(self) -> None:
            self.app.action_focus_options()

    class TextPromptApp(App[str | None]):
        CSS = PROMPT_CSS
        BINDINGS = [
            Binding("escape", "back", "Back", key_display="ESC"),
            ("ctrl+c", "copy_or_cancel", "Copy"),
            Binding("ctrl+q", "quit_prompt", "Quit", key_display="Ctrl+Q"),
        ]
        current_command_preview = initial_command_preview
        ctrl_c_copied = False

        def compose(self) -> ComposeResult:
            self.title = TEXTUAL_APP_TITLE
            yield Header()
            if has_summary_panel:
                yield from render_installation_summary(
                    initial_summary,
                    Static=Static,
                    Vertical=Vertical,
                )
            yield from render_command_preview(
                self.current_command_preview,
                Button=Button,
                Horizontal=Horizontal,
                Static=Static,
                Vertical=Vertical,
                force=command_preview_builder is not None,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=initial_preview_class,
            )
            with Vertical(id="dialog"):
                yield Static(message, id="message")
                yield PromptInput(value=default, id="text")
                with Horizontal(id="actions"):
                    yield PromptButton(submit_label, id="continue", variant="primary")
                    yield PromptButton("Back (ESC)", id="back")
                    yield PromptButton("Quit Ctrl+Q", id="quit")
            yield Footer()

        def on_mount(self) -> None:
            if has_summary_panel:
                update_installation_summary_display(self, initial_summary, Static)

        def focused_id(self) -> str | None:
            return getattr(self.screen.focused, "id", None)

        def action_quit_prompt(self) -> None:
            self.exit(None)

        def action_back(self) -> None:
            self.exit(PROMPT_BACK)

        def action_copy_or_cancel(self) -> None:
            handle_ctrl_c(self, self.current_command_preview)

        def action_focus_actions(self) -> None:
            self.query_one("#continue", PromptButton).focus()

        def action_focus_options(self) -> None:
            self.query_one("#text", PromptInput).focus()

        def action_focus_previous_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, -1)

        def action_focus_next_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, 1)

        def update_command_preview(self, value: str) -> None:
            if command_preview_builder is None:
                return
            text = value.strip() or default
            self.current_command_preview = command_preview_builder(text)
            current_summary = (
                summary_builder(text)
                if summary_builder is not None
                else summary
            )
            update_command_preview_display(
                self,
                self.current_command_preview,
                Static,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=effective_command_preview_class(
                    self.current_command_preview,
                    summary=current_summary,
                ),
            )

        def update_installation_summary(self, value: str) -> None:
            if summary_builder is None:
                return
            text = value.strip() or default
            update_installation_summary_display(
                self,
                summary_builder(text),
                Static,
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            self.update_command_preview(event.value)
            self.update_installation_summary(event.value)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            self.exit(event.value.strip() or default)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "copy-command":
                copy_command_to_clipboard(self, self.current_command_preview)
                return
            if event.button.id == "back":
                self.exit(PROMPT_BACK)
                return
            if event.button.id == "quit":
                self.exit(None)
                return
            if event.button.id == "continue":
                value = self.query_one("#text", PromptInput).value.strip()
                self.exit(value or default)

    return TextPromptApp().run()


def run_textual_version(
    message: str,
    default: str,
    choices: Sequence[dict[str, str]],
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    validator: PromptValidator | None = None,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
) -> str | None:
    return make_textual_version_app(
        message,
        default,
        choices,
        command_preview=command_preview,
        command_preview_builder=command_preview_builder,
        summary=summary,
        summary_builder=summary_builder,
        validator=validator,
        submit_label=submit_label,
    ).run()


def make_textual_version_app(
    message: str,
    default: str,
    choices: Sequence[dict[str, str]],
    *,
    command_preview: str | None = None,
    command_preview_builder: CommandPreviewBuilder | None = None,
    summary: str | None = None,
    summary_builder: CommandPreviewBuilder | None = None,
    validator: PromptValidator | None = None,
    submit_label: str = DEFAULT_SUBMIT_LABEL,
):
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, Input, OptionList, Static

    option_values = [str(choice["value"]) for choice in choices]
    initial_value = default or (option_values[0] if option_values else "")
    initial_command_preview = (
        command_preview_builder(initial_value)
        if command_preview_builder is not None
        else command_preview
    )
    initial_summary = (
        summary_builder(initial_value)
        if summary_builder is not None
        else summary
    )
    initial_preview_class = effective_command_preview_class(
        initial_command_preview,
        summary=initial_summary,
    )
    has_summary_panel = summary is not None or summary_builder is not None

    class VersionInput(Input):
        BINDINGS = [
            ("up", "focus_copy_command", "Copy command"),
            ("down", "focus_version_options", "Suggestions"),
        ]

        def action_focus_copy_command(self) -> None:
            self.app.action_focus_copy_command()

        def action_focus_version_options(self) -> None:
            self.app.action_focus_version_options()

    class VersionOptions(OptionList):
        BINDINGS = [
            Binding("up", "cursor_up_or_input", "Up", show=False),
            Binding("down", "cursor_down_or_actions", "Down", show=False),
            Binding("enter", "select", "Select", show=False),
            Binding("left,right", "focus_version_input", "Version", show=False),
        ]

        def action_cursor_up_or_input(self) -> None:
            highlighted = self.highlighted
            if highlighted is None or highlighted <= 0:
                self.app.action_focus_version_input()
                return
            self.action_cursor_up()

        def action_cursor_down_or_actions(self) -> None:
            highlighted = self.highlighted
            if highlighted is None or highlighted >= self.option_count - 1:
                self.app.action_focus_actions()
                return
            self.action_cursor_down()

        def action_focus_version_input(self) -> None:
            self.app.action_focus_version_input()

    class CopyButton(Button):
        BINDINGS = [
            ("down", "focus_version_input", "Version"),
        ]

        def action_focus_version_input(self) -> None:
            self.app.action_focus_version_input()

    class PromptButton(Button):
        BINDINGS = [
            ("left", "focus_previous_action", "Previous action"),
            ("right", "focus_next_action", "Next action"),
            ("up", "focus_options", "Options"),
        ]

        def action_focus_previous_action(self) -> None:
            self.app.action_focus_previous_action()

        def action_focus_next_action(self) -> None:
            self.app.action_focus_next_action()

        def action_focus_options(self) -> None:
            self.app.action_focus_options()

    class VersionPromptApp(App[str | None]):
        CSS = PROMPT_CSS
        BINDINGS = [
            Binding("escape", "back", "Back", key_display="ESC"),
            ("ctrl+c", "copy_or_cancel", "Copy"),
            Binding("ctrl+q", "quit_prompt", "Quit", key_display="Ctrl+Q"),
        ]
        current_command_preview = initial_command_preview
        ctrl_c_copied = False

        def compose(self) -> ComposeResult:
            self.title = TEXTUAL_APP_TITLE
            yield Header()
            if has_summary_panel:
                yield from render_installation_summary(
                    initial_summary,
                    Static=Static,
                    Vertical=Vertical,
                )
            yield from render_command_preview(
                self.current_command_preview,
                Button=CopyButton,
                Horizontal=Horizontal,
                Static=Static,
                Vertical=Vertical,
                force=command_preview_builder is not None,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=initial_preview_class,
            )
            with Vertical(id="dialog"):
                yield Static(message, id="message")
                yield VersionInput(
                    value=initial_value,
                    placeholder=message,
                    select_on_focus=False,
                    id="version",
                )
                if option_values:
                    yield VersionOptions(*option_values, id="version-options")
                yield Static("", id="error")
                with Horizontal(id="actions"):
                    yield PromptButton(submit_label, id="continue", variant="primary")
                    yield PromptButton("Back (ESC)", id="back")
                    yield PromptButton("Quit Ctrl+Q", id="quit")
            yield Footer()

        def on_mount(self) -> None:
            if has_summary_panel:
                update_installation_summary_display(self, initial_summary, Static)
            self.action_focus_version_input()

        def focused_id(self) -> str | None:
            return getattr(self.screen.focused, "id", None)

        def current_value(self) -> str:
            value = self.query_one("#version", VersionInput).value.strip()
            return value or initial_value

        def filtered_versions(self, value: str) -> list[str]:
            query = value.strip().lower()
            if not query or value.strip() == initial_value:
                return option_values
            return [
                option
                for option in option_values
                if query in option.lower()
            ]

        def refresh_version_options(self, value: str) -> None:
            options = self.query_one("#version-options", VersionOptions)
            filtered = self.filtered_versions(value)
            options.set_options(filtered)
            if filtered:
                options.highlighted = 0

        def action_quit_prompt(self) -> None:
            self.exit(None)

        def action_back(self) -> None:
            self.exit(PROMPT_BACK)

        def action_copy_or_cancel(self) -> None:
            handle_ctrl_c(self, self.current_command_preview)

        def action_focus_actions(self) -> None:
            self.query_one("#continue", PromptButton).focus()

        def action_focus_copy_command(self) -> None:
            return

        def action_focus_version_input(self) -> None:
            version_input = self.query_one("#version", VersionInput)
            version_input.focus()
            version_input.cursor_position = len(version_input.value)

        def action_focus_version_options(self) -> None:
            options = self.query_one_optional("#version-options", VersionOptions)
            if options is None or not options.display or options.option_count == 0:
                self.action_focus_actions()
                return
            if options.highlighted is None:
                options.highlighted = 0
            options.focus()

        def action_focus_options(self) -> None:
            if self.focused_id() == "continue":
                self.action_focus_version_options()
            else:
                self.action_focus_version_input()

        def action_focus_previous_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, -1)

        def action_focus_next_action(self) -> None:
            focus_prompt_action(self, self.focused_id(), PromptButton, 1)

        def update_command_preview(self, value: str) -> None:
            if command_preview_builder is None:
                return
            self.current_command_preview = command_preview_builder(
                value.strip() or initial_value
            )
            selected = value.strip() or initial_value
            current_summary = (
                summary_builder(selected)
                if summary_builder is not None
                else summary
            )
            update_command_preview_display(
                self,
                self.current_command_preview,
                Static,
                empty_message=DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
                preview_class=effective_command_preview_class(
                    self.current_command_preview,
                    summary=current_summary,
                ),
            )

        def update_installation_summary(self, value: str) -> None:
            if summary_builder is None:
                return
            update_installation_summary_display(
                self,
                summary_builder(value.strip() or initial_value),
                Static,
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "version":
                self.query_one("#error", Static).update("")
                self.update_command_preview(event.value)
                self.update_installation_summary(event.value)
                if option_values:
                    self.refresh_version_options(event.value)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "version":
                self.accept_value(event.value)

        def on_option_list_option_selected(
            self,
            event: OptionList.OptionSelected,
        ) -> None:
            if event.option_list.id != "version-options":
                return
            versions = self.filtered_versions(
                self.query_one("#version", VersionInput).value
            )
            selected_index = event.option_index
            if selected_index >= len(versions):
                return
            version = versions[selected_index]
            version_input = self.query_one("#version", VersionInput)
            version_input.value = version
            self.update_command_preview(version)
            self.update_installation_summary(version)
            self.accept_value(version)

        def action_accept_version(self) -> None:
            self.accept_value(self.current_value())

        def validation_error(self, value: str) -> str | None:
            if validator is None:
                return None
            try:
                return validator(value)
            except (InstallerError, UsageError) as error:
                return str(error)

        def accept_value(self, value: str) -> None:
            selected = value.strip() or initial_value
            error = self.validation_error(selected)
            if error:
                self.query_one("#error", Static).update(error)
                self.action_focus_version_input()
                return
            self.exit(selected)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "copy-command":
                copy_command_to_clipboard(self, self.current_command_preview)
                return
            if event.button.id == "back":
                self.exit(PROMPT_BACK)
                return
            if event.button.id == "quit":
                self.exit(None)
                return
            if event.button.id == "continue":
                self.action_accept_version()

    return VersionPromptApp()


PROMPT_CSS = """
Screen {
    layout: vertical;
    padding: 1 2;
}

#command-preview,
#installation-summary,
#dialog {
    width: 100%;
    max-width: 100%;
    height: auto;
    padding: 1 2;
    border: solid $accent;
}

#command-preview {
    height: 11;
    margin-bottom: 1;
}

#installation-summary {
    height: 8;
    margin-bottom: 1;
    background: #cfefff;
    color: #0b2a3a;
    border: solid #5aaed6;
}

#installation-summary-title {
    text-style: bold;
    margin-bottom: 1;
}

#installation-summary-content {
    width: 100%;
    height: 3;
    color: #12384a;
}

#command-preview.install-preview {
    background: #8ecf8e;
    color: #0b210b;
    border: solid #2f8f2f;
}

#command-preview.uninstall-preview {
    background: #eda9a9;
    color: #2c1010;
    border: solid #b64242;
}

#command-preview-header {
    height: auto;
    align: left middle;
}

#command-preview-title {
    width: 1fr;
    text-style: bold;
}

#command-preview-command {
    width: 100%;
    height: 3;
    color: $text-muted;
}

#command-preview.install-preview #command-preview-command {
    color: #223022;
}

#command-preview.uninstall-preview #command-preview-command {
    color: #302222;
}

#message {
    margin-bottom: 1;
    text-style: bold;
}

#choice-details {
    width: 100%;
    margin-top: 1;
    color: $text-muted;
}

#actions {
    height: auto;
    margin-top: 1;
}

#version {
    width: 100%;
}

#error {
    height: 1;
    color: $error;
}

Select,
Input,
OptionList,
SelectionList {
    width: 100%;
}

#version-options {
    height: 8;
    margin-top: 1;
    border: round $accent;
}

RadioSet {
    width: 100%;
    border: round $accent;
}

SelectionList {
    height: auto;
    max-height: 12;
    border: round $accent;
}

Button {
    margin-right: 1;
}
"""


def build_parser(project: SkillProject) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=project.command_name,
        description=f"Install or uninstall the {project.skill_name} skill.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable the interactive text UI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {project.version}",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show installed skill, source, and hook paths.",
    )

    subparsers = parser.add_subparsers(dest="command")
    for command in ("install", "uninstall"):
        subparser = subparsers.add_parser(
            command,
            help=f"{command.capitalize()} the skill and discoverability hook.",
        )
        subparser.add_argument(
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Show installed skill, source, and hook paths.",
        )
        subparser.add_argument(
            "--agent",
            metavar="AGENT[,AGENT...]",
            help="Agent integration to target.",
        )
        subparser.add_argument(
            "--scope",
            type=parse_scope_arg,
            help="Install location scope: global agent config home or a directory.",
        )
        subparser.add_argument(
            "--target-dir",
            dest="repo",
            metavar="PATH",
            type=Path,
            help="Directory to use with --scope dir. Defaults to cwd.",
        )
        subparser.add_argument(
            "--repo",
            dest="repo_target",
            action="store_true",
            help="With --scope dir, resolve and require a Git/Sapling repository root.",
        )
        subparser.add_argument(
            "--codex-home",
            type=Path,
            help="Codex home directory for global scope. Defaults to ~/.codex.",
        )
        subparser.add_argument(
            "--claude-home",
            type=Path,
            help="Claude Code home directory for global scope. Defaults to ~/.claude.",
        )
        subparser.add_argument(
            "--home",
            type=Path,
            help=argparse.SUPPRESS,
        )
        if command == "install":
            subparser.add_argument(
                "--force",
                action="store_true",
                help="Replace an existing unowned skill directory.",
            )
            subparser.add_argument(
                "--editable",
                action="store_true",
                default=None,
                help=(
                    "Install symlinks to this checkout's skill files. "
                    "Requires running inside the skill project repo."
                ),
            )
            subparser.add_argument(
                "--pypi",
                action="store_true",
                default=None,
                help=(
                    f"Resolve the latest compatible {project.pypi_name} wheel "
                    "with pip and install its bundled skill files."
                ),
            )
            subparser.add_argument(
                "--pypi-version",
                metavar="VERSION",
                help=(
                    f"Download this {project.pypi_name} wheel from PyPI and install "
                    "its bundled skill files without installing the package."
                ),
            )
            subparser.add_argument(
                "--github-url",
                metavar="URL",
                help=(
                    "Download a GitHub repository archive and install SKILL.md "
                    "from the repository root, skill/, or a tree URL path."
                ),
            )
            subparser.add_argument(
                "--github-ref",
                metavar="REF",
                help="Git ref to archive when --github-url points at a repository root.",
            )
            subparser.add_argument(
                "--github-path",
                metavar="PATH",
                help="Skill directory inside the GitHub archive.",
            )
    return parser


def strip_ui_flags(argv: Sequence[str]) -> tuple[list[str], bool]:
    stripped: list[str] = []
    no_ui = False
    for item in argv:
        if item == "--no-ui":
            no_ui = True
        else:
            stripped.append(item)
    return stripped, no_ui


def command_choices() -> list[dict[str, str]]:
    return [
        {"name": "Install", "value": "install"},
        {"name": "Uninstall", "value": "uninstall"},
    ]


def install_source_choices(project: SkillProject) -> list[dict[str, str]]:
    metadata = install_source_metadata(project)
    packaged_choices = [
        {
            "name": f"Bundled skill copy "
            f"(version {metadata.packaged_version}, no network)",
            "value": "copy",
        },
        {
            "name": (
                "PyPI wheel "
                "(requires network; pip resolves compatible package)"
            ),
            "value": "pypi",
        },
        {
            "name": (
                "GitHub repository URL "
                "(requires network; accepts root or /tree/ref/path URLs)"
            ),
            "value": "github",
        },
    ]
    if not metadata.editable_available:
        return packaged_choices

    local_version = metadata.local_version or metadata.packaged_version
    vcs = metadata.vcs or "repo"
    commit = metadata.commit or "unknown"
    state = (
        "dirty"
        if metadata.dirty is True
        else "clean"
        if metadata.dirty is False
        else "dirty unknown"
    )
    return [
        {
            "name": (
                "Editable local checkout "
                f"(version {local_version}, {vcs} {commit}, {state})"
            ),
            "value": "editable",
        },
        *packaged_choices,
    ]


def quote_command(parts: Sequence[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def split_agent_arg(value: str) -> list[str]:
    raw_parts = value.split(",")
    parts = [part.strip() for part in raw_parts]
    if any(not part for part in parts):
        raise UsageError(f"unknown agent target: {value}")
    if TARGET_ALL in parts:
        if len(parts) > 1:
            raise UsageError("--agent all cannot be combined with explicit agents")
        return list(AGENTS)

    agents: list[str] = []
    for agent in parts:
        if agent not in AGENT_TARGET_VALUES:
            raise UsageError(f"unknown agent target: {agent}")
        if agent not in agents:
            agents.append(agent)
    if not agents:
        raise UsageError("choose at least one agent")
    return agents


def agent_arg_for_agents(agents: Sequence[str], *, prefer_all: bool) -> str:
    selected: list[str] = []
    for agent in agents:
        if agent not in AGENT_TARGET_VALUES:
            raise UsageError(f"unknown agent target: {agent}")
        if agent not in selected:
            selected.append(agent)
    if not selected:
        raise UsageError("choose at least one agent")
    if set(selected) == set(AGENTS):
        return TARGET_ALL if prefer_all else ",".join(
            agent for agent in AGENTS if agent in selected
        )
    return ",".join(selected)


def normalize_agent_arg(value: str) -> str:
    value = value.strip()
    if value == TARGET_ALL:
        return TARGET_ALL
    return agent_arg_for_agents(split_agent_arg(value), prefer_all=False)


def agent_arg_from_values(values: Sequence[str]) -> str:
    if TARGET_ALL in values:
        return TARGET_ALL
    return agent_arg_for_agents(selected_agents_from_values(values), prefer_all=False)


def agent_arg_from_targets(
    targets: Sequence[InstallationTarget],
    *,
    preferred_agent: str | None = None,
) -> str:
    agents = [
        agent
        for agent in AGENTS
        if any(target[0] == agent for target in targets)
    ]
    if preferred_agent:
        preferred_agents = selected_agents_for_command(preferred_agent)
        if set(preferred_agents) == set(agents):
            return normalize_agent_arg(preferred_agent)
    return agent_arg_for_agents(agents, prefer_all=True)


def scope_arg_from_targets(targets: Sequence[InstallationTarget]) -> str:
    scopes = {scope for _, scope, _ in targets}
    if len(scopes) != 1:
        raise UsageError("cannot represent selected targets as one --scope value")
    return scopes.pop()


def repo_target_arg_from_targets(targets: Sequence[InstallationTarget]) -> bool:
    repo_targets = {repo_target for _, scope, repo_target in targets if scope == "dir"}
    if len(repo_targets) > 1:
        raise UsageError("cannot represent selected targets as one --repo value")
    return repo_targets.pop() if repo_targets else False


def selected_agents_for_command(agent: str) -> list[str]:
    return split_agent_arg(agent)


def parse_scope_arg(value: str) -> str:
    if value in {"dir", "global", "repo"}:
        return value
    raise argparse.ArgumentTypeError("scope must be global or dir")


def normalize_args_scope(args: argparse.Namespace) -> None:
    if getattr(args, "scope", None) == "repo":
        args.scope = "dir"
        args.repo_target = True


def build_no_ui_command(
    project: SkillProject,
    args: argparse.Namespace | str,
    *,
    targets: Sequence[InstallationTarget] | None = None,
    agent: str | None = None,
    scope: str | None = None,
    repo_target: bool | None = None,
    editable: bool | None = None,
    pypi: bool | None = None,
    pypi_version: str | None = None,
    github_url: str | None = None,
    github_ref: str | None = None,
    github_path: str | None = None,
    repo: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> str | None:
    if isinstance(args, str):
        args = argparse.Namespace(command=args, verbose=False)
    normalize_args_scope(args)
    if scope == "repo":
        scope = "dir"
        repo_target = True
    command = getattr(args, "command", None)
    if command not in {"install", "uninstall"}:
        return None

    if targets:
        target_groups = list(
            dict.fromkeys(
                (target_scope, target_repo_target)
                for _, target_scope, target_repo_target in targets
            )
        )
        if len(target_groups) > 1:
            commands = [
                build_no_ui_command(
                    project,
                    args,
                    targets=[
                        target
                        for target in targets
                        if target[1] == target_scope
                        and target[2] == target_repo_target
                    ],
                    agent=agent,
                    editable=editable,
                    pypi=pypi,
                    pypi_version=pypi_version,
                    github_url=github_url,
                    github_ref=github_ref,
                    github_path=github_path,
                    repo=repo,
                    codex_home=codex_home,
                    claude_home=claude_home,
                )
                for target_scope, target_repo_target in target_groups
            ]
            return "\n".join(command for command in commands if command)
        agent = agent_arg_from_targets(
            targets,
            preferred_agent=agent or getattr(args, "agent", None),
        )
        scope = scope_arg_from_targets(targets)
        repo_target = repo_target_arg_from_targets(targets)

    agent = agent or getattr(args, "agent", None) or TARGET_ALL
    repo_target = (
        repo_target
        if repo_target is not None
        else bool(getattr(args, "repo_target", False))
    )
    scope = scope or getattr(args, "scope", None) or ("dir" if repo_target else "global")
    editable = (
        editable
        if editable is not None
        else bool(getattr(args, "editable", False))
    )
    pypi = (
        pypi
        if pypi is not None
        else bool(getattr(args, "pypi", False))
    )
    pypi_version = (
        pypi_version
        if pypi_version is not None
        else getattr(args, "pypi_version", None)
    )
    github_url = (
        github_url
        if github_url is not None
        else getattr(args, "github_url", None)
    )
    github_ref = (
        github_ref
        if github_ref is not None
        else getattr(args, "github_ref", None)
    )
    github_path = (
        github_path
        if github_path is not None
        else getattr(args, "github_path", None)
    )
    parts: list[object] = [project.command_name, "--no-ui", command]
    if bool(getattr(args, "verbose", False)):
        parts.append("--verbose")
    if command == "install":
        if bool(getattr(args, "force", False)):
            parts.append("--force")
        if editable:
            parts.append("--editable")
        elif pypi:
            parts.append("--pypi")
        elif pypi_version:
            parts.extend(["--pypi-version", pypi_version])
        elif github_url:
            parts.extend(["--github-url", github_url])
            if github_ref:
                parts.extend(["--github-ref", github_ref])
            if github_path:
                parts.extend(["--github-path", github_path])

    parts.extend(["--agent", agent, "--scope", scope])
    if scope == "dir" and repo_target:
        parts.append("--repo")

    repo = repo if repo is not None else getattr(args, "repo", None)
    codex_home = (
        codex_home if codex_home is not None else getattr(args, "codex_home", None)
    )
    claude_home = (
        claude_home
        if claude_home is not None
        else getattr(args, "claude_home", None)
    )

    if scope == "dir" and repo is not None:
        parts.extend(["--target-dir", repo])
    elif scope == "global":
        selected_agents = selected_agents_for_command(agent)
        if "codex" in selected_agents and codex_home is not None:
            parts.extend(["--codex-home", codex_home])
        if "claude" in selected_agents and claude_home is not None:
            parts.extend(["--claude-home", claude_home])

    return quote_command(parts)


def scope_choices() -> list[dict[str, str]]:
    return [
        {"name": SCOPE_LABELS[scope], "value": scope}
        for scope in SCOPES
    ]


def target_choices() -> list[dict[str, str]]:
    choices = [{"name": "All", "value": TARGET_ALL}]
    for agent in AGENTS:
        choices.append(
            {
                "name": AGENT_LABELS[agent],
                "value": agent,
            }
        )
    return choices


def selected_agents_from_values(selected_agents: Sequence[str]) -> list[str]:
    if TARGET_ALL in selected_agents:
        return list(AGENTS)
    agents: list[str] = []
    for value in selected_agents:
        for agent in split_agent_arg(value):
            if agent not in agents:
                agents.append(agent)
    if not agents:
        raise UsageError("choose at least one agent")
    return agents


def installation_target_value(agent: str, scope: str, repo_target: bool = False) -> str:
    suffix = ":repo" if repo_target else ":dir"
    return f"{agent}{INSTALLATION_TARGET_SEPARATOR}{scope}{suffix}"


def parse_installation_target(value: str) -> InstallationTarget:
    agent, separator, rest = value.partition(INSTALLATION_TARGET_SEPARATOR)
    scope, _, repo_part = rest.partition(INSTALLATION_TARGET_SEPARATOR)
    repo_target = repo_part == "repo"
    if not separator or agent not in AGENTS or scope not in SCOPES:
        raise UsageError(f"unknown installation target: {value}")
    return agent, scope, repo_target


def find_ui_repo_root(args: argparse.Namespace) -> Path | None:
    repo = getattr(args, "repo", None)
    return find_repo_root(repo) if repo is not None else find_repo_root(default_repo_path())


def repo_label(repo: Path | None) -> str:
    if repo is None:
        return "repo"
    return repo.name or "repo"


def installation_choice_label(
    scope: str,
    *,
    repo_target: bool = False,
    repo: Path | None,
    status: InstallationStatus | None = None,
) -> str:
    if scope == "global":
        label = "global"
    elif repo_target:
        label = f"repository directory ({repo_label(repo)})"
    else:
        label = f"directory ({repo_label(repo)})"
    if status is not None:
        label = f"{label} ({installed_status_phrase(status)})"
    return label


def agent_home_path(
    agent: str,
    *,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> Path:
    base_home = (home or Path.home()).expanduser()
    if agent == "codex":
        return (codex_home or base_home / ".codex").expanduser()
    if agent == "claude":
        return (claude_home or base_home / ".claude").expanduser()
    raise UsageError(f"unknown agent target: {agent}")


def installation_scope_choice_label(
    scope: str,
    *,
    agents: Sequence[str],
    repo: Path | None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> str:
    if scope == SPECIFIC_DIRECTORY_VALUE:
        return "Directory"
    if scope == "global":
        return "Agent config directory"
    return (
        "Current directory (repository)"
        if repo is not None
        else "Current directory"
    )


def directory_repository_summary(directory: Path) -> str:
    repo = find_repo_root(directory)
    if repo is not None:
        return f"Directory: {directory} (repository: {repo})"
    return f"Directory: {directory} (repository: not detected)"


def installation_scope_choice_description(
    scope: str,
    *,
    agents: Sequence[str],
    repo: Path | None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> str:
    if scope == SPECIFIC_DIRECTORY_VALUE:
        return "Install files into an explicit directory; automatic discovery is not implied"
    if scope == "global":
        paths = [
            str(
                agent_home_path(
                    agent,
                    home=home,
                    codex_home=codex_home,
                    claude_home=claude_home,
                )
            )
            for agent in agents
        ]
        return "\n".join(["Install in agent config directory", *paths])
    if repo is None:
        return "Install into a detected Git or Sapling repository root"
    return directory_repository_summary(repo)


def installed_statuses_by_target(
    project: SkillProject,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> dict[InstallationTarget, InstallationStatus]:
    return {
        (status.agent, status.scope, status.repo_target): status
        for status in inspect_installations(
            project,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )
        if status.status == "installed"
    }


def installation_option_choices(
    project: SkillProject,
    agents: Sequence[str],
    *,
    repo_available: bool,
    command: str = "install",
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> list[dict[str, object]]:
    if command == "install":
        choices = [
            {
                "name": installation_scope_choice_label(
                    "global",
                    agents=agents,
                    repo=repo,
                    home=home,
                    codex_home=codex_home,
                    claude_home=claude_home,
                ),
                "description": installation_scope_choice_description(
                    "global",
                    agents=agents,
                    repo=repo,
                    home=home,
                    codex_home=codex_home,
                    claude_home=claude_home,
                ),
                "value": "global",
                "kind": "scope",
            }
        ]
        if repo_available:
            choices.append(
                {
                    "name": installation_scope_choice_label(
                        "repo",
                        agents=agents,
                        repo=repo,
                        home=home,
                        codex_home=codex_home,
                        claude_home=claude_home,
                    ),
                    "description": installation_scope_choice_description(
                        "repo",
                        agents=agents,
                        repo=repo,
                        home=home,
                        codex_home=codex_home,
                        claude_home=claude_home,
                    ),
                    "value": "repo",
                    "kind": "scope",
                }
            )
        choices.append(
            {
                "name": installation_scope_choice_label(
                    SPECIFIC_DIRECTORY_VALUE,
                    agents=agents,
                    repo=repo,
                    home=home,
                    codex_home=codex_home,
                    claude_home=claude_home,
                ),
                "description": installation_scope_choice_description(
                    SPECIFIC_DIRECTORY_VALUE,
                    agents=agents,
                    repo=repo,
                    home=home,
                    codex_home=codex_home,
                    claude_home=claude_home,
                ),
                "value": SPECIFIC_DIRECTORY_VALUE,
                "kind": "scope",
            }
        )
        return choices

    installed_by_target = (
        installed_statuses_by_target(
            project,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )
        if command == "uninstall"
        else {}
    )
    choices = [{"name": "All", "value": TARGET_ALL, "kind": "all"}]
    for agent in agents:
        target_choices: list[dict[str, object]] = []
        for scope, repo_target in (
            ("global", False),
            ("dir", True),
            ("dir", False),
        ):
            if scope == "dir" and repo_target and not repo_available:
                continue
            status = installed_by_target.get((agent, scope, repo_target))
            if command == "uninstall" and status is None:
                continue
            target_choices.append(
                {
                    "name": "    "
                    + installation_choice_label(
                        scope,
                        repo_target=repo_target,
                        repo=repo,
                        status=status,
                    ),
                    "value": installation_target_value(agent, scope, repo_target),
                    "kind": "target",
                }
            )
        if not target_choices:
            continue
        choices.append(
            {
                "name": f"  {AGENT_LABELS[agent]}",
                "value": f"agent{INSTALLATION_TARGET_SEPARATOR}{agent}",
                "kind": "group",
                "disabled": True,
            }
        )
        choices.extend(target_choices)
    if len(choices) == 1:
        return []
    return choices


def default_installation_option_values(agents: Sequence[str]) -> list[str]:
    return [installation_target_value(agent, "global") for agent in agents]


def normalize_installation_targets(
    selected_options: Sequence[str],
    *,
    project: SkillProject,
    agents: Sequence[str],
    repo_available: bool,
    command: str = "install",
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> list[InstallationTarget]:
    if isinstance(selected_options, str):
        selected_options = [selected_options]
    if command == "install":
        available_scopes = [
            str(choice["value"])
            for choice in installation_option_choices(
                project,
                agents,
                repo_available=repo_available,
                command=command,
                repo=repo,
                home=home,
                codex_home=codex_home,
                claude_home=claude_home,
            )
            if choice.get("kind") == "scope"
        ]
        scopes: list[str] = []
        for selected in selected_options:
            scope = str(selected)
            if scope not in available_scopes:
                raise UsageError(f"unknown installation target: {scope}")
            if scope not in scopes:
                scopes.append(scope)
        if not scopes:
            raise UsageError("choose at least one installation target")
        targets: list[InstallationTarget] = []
        for selected_scope in scopes:
            scope = "dir" if selected_scope in {"repo", SPECIFIC_DIRECTORY_VALUE} else selected_scope
            repo_target = selected_scope == "repo"
            targets.extend((agent, scope, repo_target) for agent in agents)
        return targets

    available = [
        parse_installation_target(choice["value"])
        for choice in installation_option_choices(
            project,
            agents,
            repo_available=repo_available,
            command=command,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )
        if choice.get("kind", "target") == "target"
    ]
    if TARGET_ALL in selected_options:
        return available

    targets: list[InstallationTarget] = []
    for selected in selected_options:
        target = parse_installation_target(selected)
        if target not in available:
            raise UsageError(f"unknown installation target: {selected}")
        if target not in targets:
            targets.append(target)

    if not targets:
        raise UsageError("choose at least one installation target")
    return targets


def installed_status_phrase(status: InstallationStatus) -> str:
    phrase = f"version {status.version}" if status.version else "installed, version unknown"
    if status.install_mode == "editable":
        phrase += ", editable"
    return phrase


def installation_summary_text(
    project: SkillProject,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> str:
    statuses = inspect_installations(
        project,
        repo=repo,
        home=home,
        codex_home=codex_home,
        claude_home=claude_home,
    )
    by_target = {}
    for status in statuses:
        scope = "dir" if status.scope == "repo" else status.scope
        repo_target = True if status.scope == "repo" else status.repo_target
        by_target[(status.agent, scope, repo_target)] = status

    lines: list[str] = []
    repo_statuses = [by_target[(agent, "dir", True)] for agent in AGENTS]
    global_statuses = [by_target[(agent, "global", False)] for agent in AGENTS]

    repo_available = any(status.status != "unavailable" for status in repo_statuses)
    repo_installed = [
        status for status in repo_statuses if status.status == "installed"
    ]
    global_installed = [
        status for status in global_statuses if status.status == "installed"
    ]

    for status in repo_installed:
        lines.append(
            f"{AGENT_LABELS[status.agent]} in repo: "
            f"{installed_status_phrase(status)}"
        )
    if repo_available and not repo_installed:
        lines.append("Not installed in repo")

    for status in global_installed:
        lines.append(
            f"{AGENT_LABELS[status.agent]} in home dir: "
            f"{installed_status_phrase(status)}"
        )
    if not global_installed:
        lines.append("Not installed in home dir")

    return "\n".join(lines)


def normalize_targets(
    selected_targets: Sequence[str],
    scope: str,
    repo_target: bool = False,
) -> list[InstallationTarget]:
    if TARGET_ALL in selected_targets:
        return [(agent, scope, repo_target) for agent in AGENTS]

    targets: list[InstallationTarget] = []
    for selected in selected_targets:
        if selected not in AGENT_TARGET_VALUES:
            raise UsageError(f"unknown installation target: {selected}")
        target = (selected, scope, repo_target)
        if target not in targets:
            targets.append(target)

    if not targets:
        raise UsageError("choose at least one installation target")
    return targets


def targets_from_args(args: argparse.Namespace) -> list[InstallationTarget] | None:
    if getattr(args, "agent", None) is None or getattr(args, "scope", None) is None:
        return None

    agents = selected_agents_for_command(args.agent)
    return [(agent, args.scope, bool(getattr(args, "repo_target", False))) for agent in agents]


def complete_with_ui(
    project: SkillProject,
    args: argparse.Namespace,
    prompter: Prompter | None = None,
) -> argparse.Namespace:
    prompter = prompter or TextualPrompter(project)
    print(f"{project.skill_name} installer")

    missing = object()
    history: list[Callable[[], None]] = []

    def capture(fields: Sequence[str]) -> dict[str, object]:
        return {
            field: getattr(args, field, missing)
            for field in fields
        }

    def restore(snapshot: dict[str, object]) -> None:
        for field, value in snapshot.items():
            if value is missing:
                if hasattr(args, field):
                    delattr(args, field)
            else:
                setattr(args, field, value)

    def prompt_step(
        fields: Sequence[str],
        prompt: Callable[[], object],
    ) -> object:
        snapshot = capture(fields)
        try:
            result = prompt()
        except BackRequested:
            if not history:
                raise KeyboardInterrupt from None
            history.pop()()
            return PROMPT_BACK
        history.append(lambda snapshot=snapshot: restore(snapshot))
        return result

    def uninstall_summary() -> str | None:
        if getattr(args, "command", None) != "uninstall":
            return None
        return installation_summary_text(
            project,
            repo=getattr(args, "repo", None),
            home=getattr(args, "home", None),
            codex_home=getattr(args, "codex_home", None),
            claude_home=getattr(args, "claude_home", None),
        )

    def final_submit_label() -> str:
        command = str(getattr(args, "command", "")).strip()
        if command in {"install", "uninstall"}:
            return command.capitalize()
        return DEFAULT_SUBMIT_LABEL

    while True:
        if args.command is None:
            def command_preview(command: object) -> str | None:
                preview_args = argparse.Namespace(**vars(args))
                preview_args.command = str(command)
                return build_no_ui_command(project, preview_args)

            command = prompt_step(
                [
                    "command",
                    "editable",
                    "pypi",
                    "pypi_version",
                    "github_url",
                    "github_ref",
                    "github_path",
                    "scope",
                    "repo_target",
                    "selected_agents",
                    "targets",
                    "repo",
                    "codex_home",
                    "claude_home",
                ],
                lambda: prompter.select(
                    f"What would you like to do with {project.skill_name}?",
                    command_choices(),
                    command_preview_builder=command_preview,
                ),
            )
            if command == PROMPT_BACK:
                continue
            args.command = str(command)
            continue

        if (
            args.command == "install"
            and getattr(args, "editable", None) is None
            and getattr(args, "pypi", None) is None
            and getattr(args, "pypi_version", None) is None
            and getattr(args, "github_url", None) is None
        ):
            source_choices = install_source_choices(project)
            if len(source_choices) > 1:
                def install_source_preview(source: object) -> str | None:
                    source_value = str(source)
                    return build_no_ui_command(
                        project,
                        args,
                        editable=source_value == "editable",
                        pypi=source_value == "pypi",
                        github_url=(
                            f"https://github.com/OWNER/{project.skill_name}"
                            if source_value == "github"
                            else None
                        ),
                    )

                install_source = prompt_step(
                    [
                        "editable",
                        "pypi",
                        "pypi_version",
                        "github_url",
                        "github_ref",
                        "github_path",
                    ],
                    lambda: prompter.select(
                        f"Install source for {project.skill_name}",
                        source_choices,
                        command_preview_builder=install_source_preview,
                    ),
                )
                if install_source == PROMPT_BACK:
                    continue
                if install_source == "editable":
                    args.editable = True
                    args.pypi = False
                    args.pypi_version = None
                    args.github_url = None
                    args.github_ref = None
                    args.github_path = None
                elif install_source == "pypi":
                    args.editable = False
                    args.pypi = True
                    args.pypi_version = None
                    args.github_url = None
                    args.github_ref = None
                    args.github_path = None
                elif install_source == "github":
                    args.editable = False
                    args.pypi = False
                    args.pypi_version = None
                    args.github_url = ""
                else:
                    args.editable = False
                    args.pypi = False
                    args.pypi_version = None
                    args.github_url = None
                    args.github_ref = None
                    args.github_path = None
                continue
            args.editable = False

        if args.command == "install" and getattr(args, "github_url", None) == "":
            def github_url_preview(url: object) -> str | None:
                value = str(url).strip()
                return build_no_ui_command(
                    project,
                    args,
                    github_url=value or f"https://github.com/OWNER/{project.skill_name}",
                )

            github_url = prompt_step(
                ["github_url"],
                lambda: prompter.text(
                    "GitHub repository URL",
                    "",
                    command_preview_builder=github_url_preview,
                ),
            )
            if github_url == PROMPT_BACK:
                continue
            args.github_url = str(github_url).strip()
            if not args.github_url:
                raise UsageError("GitHub URL must not be empty")
            continue

        targets = getattr(args, "targets", None)
        if targets is None:
            targets = targets_from_args(args)
        if targets is None:
            selected_agents = getattr(args, "selected_agents", None)
            if selected_agents is None:
                if getattr(args, "agent", None) is not None:
                    args.agent = normalize_agent_arg(args.agent)
                    selected_agents = selected_agents_for_command(args.agent)
                    args.selected_agents = selected_agents
                    continue

                def agent_preview(selected_agents_value: object) -> str | None:
                    selected = (
                        list(selected_agents_value)
                        if isinstance(selected_agents_value, (list, tuple, set))
                        else [str(selected_agents_value)]
                    )
                    if not selected:
                        return None
                    agents = selected_agents_from_values(selected)
                    return build_no_ui_command(
                        project,
                        args,
                        targets=[(agent, "global", False) for agent in agents],
                        agent=agent_arg_from_values(selected),
                    )

                selected_agent_values = prompt_step(
                    [
                        "selected_agents",
                        "targets",
                        "repo_target",
                        "repo",
                        "codex_home",
                        "claude_home",
                    ],
                    lambda: prompter.checkbox(
                        f"Select agents for {project.skill_name}",
                        target_choices(),
                        command_preview_builder=agent_preview,
                        summary=(
                            f"Uninstalling {project.skill_name}"
                            if getattr(args, "command", None) == "uninstall"
                            else None
                        ),
                        default_values=(
                            [TARGET_ALL]
                            if getattr(args, "command", None) == "install"
                            else None
                        ),
                    ),
                )
                if selected_agent_values == PROMPT_BACK:
                    continue
                selected_agents = selected_agents_from_values(selected_agent_values)
                args.agent = agent_arg_from_values(selected_agent_values)
                args.selected_agents = selected_agents
                continue

            repo_root = find_ui_repo_root(args)
            repo_available = repo_root is not None
            option_choices = installation_option_choices(
                project,
                selected_agents,
                repo_available=repo_available,
                command=args.command,
                repo=repo_root,
                home=getattr(args, "home", None),
                codex_home=getattr(args, "codex_home", None),
                claude_home=getattr(args, "claude_home", None),
            )
            if not option_choices:
                raise UsageError(
                    f"no installed {project.skill_name} skills match the selected agents"
                )

            def installation_preview(selected_options: object) -> str | None:
                selected = (
                    list(selected_options)
                    if isinstance(selected_options, (list, tuple, set))
                    else [str(selected_options)]
                )
                if not selected:
                    return None
                if args.command == "install" and selected == [SPECIFIC_DIRECTORY_VALUE]:
                    return None
                preview_targets = normalize_installation_targets(
                    selected,
                    project=project,
                    agents=selected_agents,
                    repo_available=repo_available,
                    command=args.command,
                    repo=repo_root,
                    home=getattr(args, "home", None),
                    codex_home=getattr(args, "codex_home", None),
                    claude_home=getattr(args, "claude_home", None),
                )
                return build_no_ui_command(
                    project,
                    args,
                    targets=preview_targets,
                    repo=repo_root,
                )

            if args.command == "install":
                selected_options = prompt_step(
                    ["targets", "repo_target", "repo", "codex_home", "claude_home"],
                    lambda: prompter.select(
                        f"Install location for {project.skill_name}",
                        option_choices,
                        command_preview_builder=installation_preview,
                        submit_label=final_submit_label(),
                    ),
                )
            else:
                default_options = default_installation_option_values(selected_agents)
                selected_options = prompt_step(
                    ["targets", "repo_target", "repo", "codex_home", "claude_home"],
                    lambda: prompter.checkbox(
                        f"Select {project.skill_name} installations",
                        option_choices,
                        command_preview_builder=installation_preview,
                        summary=uninstall_summary(),
                        default_values=default_options,
                        submit_label=final_submit_label(),
                    ),
                )
            if selected_options == PROMPT_BACK:
                continue
            if (
                args.command == "install"
                and str(selected_options) == SPECIFIC_DIRECTORY_VALUE
            ):
                selected_targets = [(agent, "dir", False) for agent in selected_agents]

                def specific_repo_preview(repo: object) -> str | None:
                    return build_no_ui_command(
                        project,
                        args,
                        targets=selected_targets,
                        repo=Path(str(repo)),
                    )

                repo = prompt_step(
                    ["targets", "repo_target", "repo"],
                    lambda: prompter.path(
                        "Directory path",
                        default_repo_path(),
                        command_preview_builder=specific_repo_preview,
                        summary_builder=directory_repository_summary,
                        submit_label=final_submit_label(),
                    ),
                )
                if repo == PROMPT_BACK:
                    continue
                args.targets = selected_targets
                args.repo_target = False
                args.repo = repo
                continue
            targets = normalize_installation_targets(
                selected_options,
                project=project,
                agents=selected_agents,
                repo_available=repo_available,
                command=args.command,
                repo=repo_root,
                home=getattr(args, "home", None),
                codex_home=getattr(args, "codex_home", None),
                claude_home=getattr(args, "claude_home", None),
            )
            args.targets = targets
            args.repo_target = repo_target_arg_from_targets(targets)
            if (
                any(scope == "dir" and repo_target for _, scope, repo_target in targets)
                and getattr(args, "repo", None) is None
            ):
                args.repo = repo_root
            continue

        args.targets = targets

        if (
            any(scope == "dir" for _, scope, _ in targets)
            and getattr(args, "repo", None) is None
        ):
            def repo_preview(repo: object) -> str | None:
                return build_no_ui_command(
                    project,
                    args,
                    targets=targets,
                    repo=Path(str(repo)),
                )

            repo = prompt_step(
                ["repo_target", "repo"],
                lambda: prompter.path(
                    "Directory path",
                    default_repo_path(),
                    command_preview_builder=repo_preview,
                    summary_builder=directory_repository_summary,
                    submit_label=final_submit_label(),
                ),
            )
            if repo == PROMPT_BACK:
                continue
            args.repo = repo
            continue

        break

    if not hasattr(args, "force"):
        args.force = False
    if not hasattr(args, "home"):
        args.home = None
    if not hasattr(args, "codex_home"):
        args.codex_home = None
    if not hasattr(args, "claude_home"):
        args.claude_home = None
    if not hasattr(args, "repo_target"):
        args.repo_target = False
    if not hasattr(args, "repo"):
        args.repo = None
    if not hasattr(args, "editable") or args.editable is None:
        args.editable = False
    if not hasattr(args, "pypi") or args.pypi is None:
        args.pypi = False
    if not hasattr(args, "pypi_version"):
        args.pypi_version = None
    if not hasattr(args, "github_url"):
        args.github_url = None
    if not hasattr(args, "github_ref"):
        args.github_ref = None
    if not hasattr(args, "github_path"):
        args.github_path = None
    if not hasattr(args, "verbose"):
        args.verbose = False
    return args


def require_noninteractive_args(args: argparse.Namespace) -> None:
    normalize_args_scope(args)
    if args.command is None:
        raise UsageError("choose install or uninstall")
    if getattr(args, "agent", None) is None:
        raise UsageError("--agent is required when the text UI is disabled")
    args.agent = normalize_agent_arg(args.agent)
    if not hasattr(args, "repo_target"):
        args.repo_target = False
    if getattr(args, "scope", None) is None:
        if getattr(args, "repo_target", False):
            args.scope = "dir"
        else:
            raise UsageError("--scope is required when the text UI is disabled")
    if args.scope == "global" and getattr(args, "repo_target", False):
        raise UsageError("--repo can only be used with --scope dir")
    if not hasattr(args, "force"):
        args.force = False
    if not hasattr(args, "editable") or args.editable is None:
        args.editable = False
    if not hasattr(args, "pypi") or args.pypi is None:
        args.pypi = False
    if not hasattr(args, "pypi_version"):
        args.pypi_version = None
    if not hasattr(args, "github_url"):
        args.github_url = None
    if not hasattr(args, "github_ref"):
        args.github_ref = None
    if not hasattr(args, "github_path"):
        args.github_path = None
    if not hasattr(args, "verbose"):
        args.verbose = False
    if args.command == "install":
        selected_sources = [
            name
            for name, enabled in (
                ("--editable", args.editable),
                ("--pypi", args.pypi),
                ("--pypi-version", args.pypi_version is not None),
                ("--github-url", args.github_url is not None),
            )
            if enabled
        ]
        if len(selected_sources) > 1:
            raise UsageError(f"{', '.join(selected_sources)} cannot be combined")
        if args.pypi_version is not None:
            args.pypi_version = args.pypi_version.strip()
            if not args.pypi_version:
                raise UsageError("--pypi-version must not be empty")
        if args.github_url is not None:
            args.github_url = args.github_url.strip()
            if not args.github_url:
                raise UsageError("--github-url must not be empty")
        if args.github_ref is not None:
            args.github_ref = args.github_ref.strip()
            if not args.github_ref:
                raise UsageError("--github-ref must not be empty")
        if args.github_path is not None:
            args.github_path = args.github_path.strip()
            if not args.github_path:
                raise UsageError("--github-path must not be empty")
        if args.github_url is None and (
            args.github_ref is not None or args.github_path is not None
        ):
            raise UsageError("--github-ref and --github-path require --github-url")


def style_text(text: str, code: str, *, color: bool) -> str:
    if not color:
        return text
    return f"\033[{code}m{text}\033[0m"


def version_suffix(result: InstallResult) -> str:
    if result.version is None:
        return ""
    if result.action == "uninstall":
        return f" version {result.version}"

    details: list[str] = []
    if result.version_change == "upgrade":
        details.append(f"upgraded from {result.previous_version}")
    elif result.version_change == "downgrade":
        details.append(f"downgraded from {result.previous_version}")
    if result.install_mode == "editable":
        details.append("editable")
    elif result.install_mode == "pypi":
        details.append("PyPI wheel")
    elif result.install_mode == "wheel":
        details.append("wheel")
    elif result.install_mode == "github":
        details.append("GitHub archive")

    suffix = f" version {result.version}"
    if details:
        suffix += f" ({', '.join(details)})"
    return suffix


def version_label(result: InstallResult) -> str:
    return version_suffix(result).removeprefix(" version").strip()


def skill_label(result: InstallResult) -> str:
    return result.skill_dir.name


def result_target_label(result: InstallResult) -> str:
    agent = AGENT_LABELS.get(result.agent, result.agent)
    if result.scope == "global":
        return f"{agent} global"
    if result.scope == "dir":
        directory_name = result.hook_path.parent.name
        if result.repo_target:
            if directory_name and directory_name != "repo":
                return f"{agent} repo ({directory_name})"
            return f"{agent} repo"
        if directory_name:
            return f"{agent} directory ({directory_name})"
        return f"{agent} directory"
    return f"{agent} {result.scope}"


def format_status_line(result: InstallResult, *, color: bool) -> str:
    version = version_label(result)
    version_part = f" {version}" if version else ""
    target = result_target_label(result)
    if result.action == "install":
        line = (
            f"Installed {skill_label(result)}{version_part} "
            f"to {target}: {result.skill_dir}"
        )
    elif result.action == "uninstall":
        line = (
            f"Removed {skill_label(result)}{version_part} "
            f"from {target}: {result.skill_dir}"
        )
    else:
        line = (
            f"{result.status}: {describe_target(result.agent, result.scope)}"
            f"{version_suffix(result)}"
        )
    color_code = VERSION_CHANGE_COLORS.get(result.version_change or "")
    if color_code is None:
        return line
    return style_text(line, color_code, color=color)


def print_results(results: Sequence[InstallResult], *, verbose: bool = False) -> None:
    color = sys.stdout.isatty()
    for result in results:
        print(format_status_line(result, color=color))
        if not verbose:
            continue
        print(f"  skill: {result.skill_dir}")
        if result.source_dir is not None:
            print(f"  source: {result.source_dir}")
        if result.source_url is not None:
            print(f"  source: {result.source_url}")
        elif result.source_path is not None:
            print(f"  source: {result.source_path}")
        print(f"  hook:  {result.hook_path}")


def run(project: SkillProject, args: argparse.Namespace) -> list[InstallResult]:
    normalize_args_scope(args)
    installer = Installer(project)
    targets = getattr(args, "targets", None)
    if targets is None:
        agents = selected_agents_for_command(args.agent)
        repo = args.repo if args.scope == "dir" else None
        if args.command == "install":
            return installer.install(
                agents,
                args.scope,
                repo_target=getattr(args, "repo_target", False),
                repo=repo,
                home=args.home,
                codex_home=args.codex_home,
                claude_home=args.claude_home,
                force=args.force,
                editable=args.editable,
                pypi=args.pypi,
                pypi_version=args.pypi_version,
                github_url=args.github_url,
                github_ref=args.github_ref,
                github_path=args.github_path,
            )
        if args.command == "uninstall":
            return installer.uninstall(
                agents,
                args.scope,
                repo_target=getattr(args, "repo_target", False),
                repo=repo,
                home=args.home,
                codex_home=args.codex_home,
                claude_home=args.claude_home,
            )
        raise UsageError(f"unknown command: {args.command}")

    results: list[InstallResult] = []
    groups = list(dict.fromkeys((scope, repo_target) for _, scope, repo_target in targets))
    for scope, repo_target in groups:
        agents = [
            agent
            for agent, target_scope, target_repo_target in targets
            if target_scope == scope and target_repo_target == repo_target
        ]
        repo = args.repo if scope == "dir" else None
        if args.command == "install":
            results.extend(
                installer.install(
                    agents,
                    scope,
                    repo_target=repo_target,
                    repo=repo,
                    home=args.home,
                    codex_home=args.codex_home,
                    claude_home=args.claude_home,
                    force=args.force,
                    editable=args.editable,
                    pypi=args.pypi,
                    pypi_version=args.pypi_version,
                    github_url=args.github_url,
                    github_ref=args.github_ref,
                    github_path=args.github_path,
                )
            )
        elif args.command == "uninstall":
            results.extend(
                installer.uninstall(
                    agents,
                    scope,
                    repo_target=repo_target,
                    repo=repo,
                    home=args.home,
                    codex_home=args.codex_home,
                    claude_home=args.claude_home,
                )
            )
        else:
            raise UsageError(f"unknown command: {args.command}")
    return results


def should_use_ui(args: argparse.Namespace, explicit_no_ui: bool) -> bool:
    if explicit_no_ui or not running_on_tty():
        return False
    if args.command is None:
        return True
    if getattr(args, "agent", None) is None:
        return True
    return getattr(args, "scope", None) is None and not getattr(args, "repo_target", False)


def print_pypi_install_attempt(project: SkillProject, args: argparse.Namespace) -> None:
    if args.command != "install":
        return
    if getattr(args, "pypi", False):
        print(f"Installing from PyPI: {project.pypi_name}", file=sys.stderr)
        return
    if getattr(args, "pypi_version", None) is None:
        return
    print(
        f"Installing from PyPI: {project.pypi_name}=={args.pypi_version}",
        file=sys.stderr,
    )


def print_github_install_attempt(args: argparse.Namespace) -> None:
    if args.command != "install" or getattr(args, "github_url", None) is None:
        return
    print(f"Installing from GitHub: {args.github_url}", file=sys.stderr)


def print_install_attempt(project: SkillProject, args: argparse.Namespace) -> None:
    print_pypi_install_attempt(project, args)
    print_github_install_attempt(args)


def prepare_args(args: argparse.Namespace) -> None:
    if not hasattr(args, "force"):
        args.force = False
    if not hasattr(args, "home"):
        args.home = None
    if not hasattr(args, "codex_home"):
        args.codex_home = None
    if not hasattr(args, "claude_home"):
        args.claude_home = None
    if not hasattr(args, "editable") or args.editable is None:
        args.editable = False
    if not hasattr(args, "pypi") or args.pypi is None:
        args.pypi = False
    if not hasattr(args, "pypi_version"):
        args.pypi_version = None
    if not hasattr(args, "github_url"):
        args.github_url = None
    if not hasattr(args, "github_ref"):
        args.github_ref = None
    if not hasattr(args, "github_path"):
        args.github_path = None
    if not hasattr(args, "verbose"):
        args.verbose = False


def main(
    argv: Sequence[str] | None = None,
    *,
    project: SkillProject,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    argv_without_ui_flags, explicit_no_ui = strip_ui_flags(raw_argv)
    parser = build_parser(project)
    args = parser.parse_args(argv_without_ui_flags)

    try:
        if should_use_ui(args, explicit_no_ui):
            args = complete_with_ui(project, args)
        else:
            prepare_args(args)
            require_noninteractive_args(args)
        print_install_attempt(project, args)
        results = run(project, args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except UsageError as error:
        parser.print_usage(sys.stderr)
        print(f"{project.command_name}: error: {error}", file=sys.stderr)
        return 2
    except InstallerError as error:
        print(f"{project.command_name}: error: {error}", file=sys.stderr)
        return 1

    print_results(results, verbose=bool(getattr(args, "verbose", False)))
    return 0

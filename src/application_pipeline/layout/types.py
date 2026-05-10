import pathlib
from dataclasses import dataclass

from application_pipeline.user_settings import UserSettingsError


class LayoutError(UserSettingsError):
    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        resolved_path: pathlib.Path | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.resolved_path = resolved_path


@dataclass(frozen=True)
class Layout:
    tier_emoji: dict[str, str]
    tier_color: dict[str, str]
    placeholder_groups: dict[str, tuple[str, list[str]]]
    file_header: str
    card_template: str
    headline_template: str
    empty_list_placeholder: str = "—"

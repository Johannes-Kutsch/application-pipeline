from dataclasses import dataclass

from application_pipeline.user_settings import UserSettingsError


class LayoutError(UserSettingsError):
    pass


@dataclass(frozen=True)
class Layout:
    tier_emoji: dict[str, str]
    tier_color: dict[str, str]
    placeholder_groups: dict[str, tuple[str, list[str]]]
    file_header: str
    card_template: str
    headline_template: str

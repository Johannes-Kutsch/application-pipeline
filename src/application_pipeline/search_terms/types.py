from __future__ import annotations

from dataclasses import dataclass

from application_pipeline.user_settings import UserSettingsError


class SearchTermsError(UserSettingsError):
    pass


@dataclass(frozen=True)
class SearchTerms:
    keywords: tuple[str, ...]
    skills: tuple[str, ...]
    negative_keywords: tuple[str, ...]

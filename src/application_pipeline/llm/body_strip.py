"""Compatibility wrapper for body text extraction."""

from __future__ import annotations

from application_pipeline.parsers.body_text import html_to_raw_description


def strip_to_text(html: str, selector: str | None) -> str:
    """Preserve the legacy import path until parser callers are rewired."""
    return html_to_raw_description(html, selector)

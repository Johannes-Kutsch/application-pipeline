from .loader import load
from .types import Layout, LayoutError

__all__ = ["Layout", "LayoutError", "default", "load"]


def default() -> Layout:
    """Return a minimal built-in Layout used when no user layout is configured."""
    return Layout(
        tier_emoji={"green": "🟢", "amber": "🟡", "red": "🔴"},
        tier_color={"green": "#2ea043", "amber": "#d29922", "red": "#da3633"},
        placeholder_groups={},
        file_header="# Results\n\n",
        card_template="## {number}. {title}  {emoji}\n\n",
        headline_template="## {number}. {title}  {emoji}\n\n",
    )

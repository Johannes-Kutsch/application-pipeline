from .loader import load
from .types import Layout, LayoutError

__all__ = ["Layout", "LayoutError", "default", "load"]


def default() -> Layout:
    """Return a minimal built-in Layout used when no user layout is configured."""
    return Layout(
        placeholder_groups={},
        card_template="# {rank} · {title}\n\n{summary}\n\n---\n<{url}>\n",
    )

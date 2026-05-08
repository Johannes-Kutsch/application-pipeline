from __future__ import annotations

import importlib

from .errors import UnknownParserError
from .protocol import Parser


def get_parser_class(parser_type: str) -> type[Parser]:
    try:
        module = importlib.import_module(f"application_pipeline.parsers.{parser_type}")
    except ImportError:
        raise UnknownParserError(f"No parser module found for type {parser_type!r}")

    parser_class = getattr(module, "parser_class", None)
    if parser_class is None:
        raise UnknownParserError(
            f"Parser module {parser_type!r} does not expose a 'parser_class' attribute"
        )
    return parser_class  # type: ignore[return-value]

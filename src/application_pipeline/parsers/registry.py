from __future__ import annotations

import logging

from .bundesagentur_api import BundesagenturParser
from .jobs_beim_staat_html import JobsBeimStaatParser
from .protocol import Parser
from .stellen_hamburg_api import StellenHamburgParser

_log = logging.getLogger(__name__)

_PARSERS: dict[str, type[Parser]] = {
    "bundesagentur_api": BundesagenturParser,
    "stellen_hamburg_api": StellenHamburgParser,
    "jobs_beim_staat_html": JobsBeimStaatParser,
}


def get(parser_type: str) -> type[Parser] | None:
    cls = _PARSERS.get(parser_type)
    if cls is None:
        _log.warning("unknown_parser_type parser_type=%s", parser_type)
    return cls

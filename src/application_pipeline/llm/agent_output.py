import json
import re
from typing import Any, Literal


class AgentOutputProtocolError(Exception):
    def __init__(self, kind: Literal["tag_missing", "json_malformed"]) -> None:
        super().__init__(kind)
        self.kind = kind


def extract_json_block(text: str, tag: str) -> Any:
    """Extract a JSON payload from a tag-wrapped agent response.

    Finds the rightmost closing tag, walks back through all openers before it,
    strips any markdown code fence, and returns the first successfully parsed body.

    Raises AgentOutputProtocolError("tag_missing") if the tag is absent.
    Raises AgentOutputProtocolError("json_malformed") if every candidate body fails json.loads.
    """
    closing_tag = f"</{tag}>"
    opening_tag = f"<{tag}>"

    close_pos = text.rfind(closing_tag)
    if close_pos == -1:
        raise AgentOutputProtocolError("tag_missing")

    candidates: list[int] = []
    search_start = 0
    while True:
        pos = text.find(opening_tag, search_start, close_pos)
        if pos == -1:
            break
        candidates.append(pos)
        search_start = pos + 1

    if not candidates:
        raise AgentOutputProtocolError("tag_missing")

    for open_pos in reversed(candidates):
        body = text[open_pos + len(opening_tag) : close_pos].strip()
        body = _strip_fence(body)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            continue

    raise AgentOutputProtocolError("json_malformed")


def _strip_fence(body: str) -> str:
    m = re.match(r"^```(?:json)?\n(.*)\n```$", body, re.DOTALL)
    if m:
        return m.group(1)
    return body

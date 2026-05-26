import json
import re
from typing import Any, Literal


class AgentOutputProtocolError(Exception):
    def __init__(self, kind: Literal["tag_missing", "json_malformed"]) -> None:
        super().__init__(kind)
        self.kind = kind


def extract_json_block(text: str, tag: str) -> tuple[Any, bool]:
    """Extract a JSON payload from a tag-wrapped agent response.

    Returns ``(parsed_value, is_fallback)`` where ``is_fallback`` is ``True``
    when the value was recovered from a bare markdown code fence (no XML tags).

    Normal path: finds the rightmost closing tag, walks back through all
    openers before it, strips any markdown code fence, and returns the first
    successfully parsed body.

    Fallback path: when no tags are present, attempts to parse a bare markdown
    code fence directly.

    Raises AgentOutputProtocolError("tag_missing") if the tag is absent and
    no bare-fence fallback can be recovered.
    Raises AgentOutputProtocolError("json_malformed") if every candidate body
    fails json.loads.
    """
    closing_tag = f"</{tag}>"
    opening_tag = f"<{tag}>"

    close_pos = text.rfind(closing_tag)
    if close_pos == -1:
        return _fallback_bare_fence(text)

    candidates: list[int] = []
    search_start = 0
    while True:
        pos = text.find(opening_tag, search_start, close_pos)
        if pos == -1:
            break
        candidates.append(pos)
        search_start = pos + 1

    if not candidates:
        return _fallback_bare_fence(text)

    for open_pos in reversed(candidates):
        body = text[open_pos + len(opening_tag) : close_pos].strip()
        body = _strip_fence(body)
        try:
            return json.loads(body), False
        except json.JSONDecodeError:
            continue

    raise AgentOutputProtocolError("json_malformed")


def _fallback_bare_fence(text: str) -> tuple[Any, bool]:
    """Try to recover a JSON value from a bare markdown code fence.

    Raises AgentOutputProtocolError("tag_missing") if no fence is found or
    the fence body is not valid JSON.
    """
    t = text.strip()
    stripped = _strip_fence(t)
    if stripped is t:
        raise AgentOutputProtocolError("tag_missing")
    try:
        return json.loads(stripped), True
    except json.JSONDecodeError:
        raise AgentOutputProtocolError("tag_missing") from None


def extract_id_tagged_verdicts(text: str) -> dict[int, Any]:
    """Extract all ``<verdict id="N">JSON</verdict>`` tags from *text*.

    Returns a mapping of numeric id → parsed JSON value.  Tags whose body is
    not valid JSON are silently omitted so callers can treat the missing key as
    ``None``.
    """
    pattern = re.compile(r'<verdict\s+id="(\d+)">(.*?)</verdict>', re.DOTALL)
    result: dict[int, Any] = {}
    for m in pattern.finditer(text):
        item_id = int(m.group(1))
        body = _strip_fence(m.group(2).strip())
        try:
            result[item_id] = json.loads(body)
        except json.JSONDecodeError:
            pass
    return result


def _strip_fence(body: str) -> str:
    m = re.match(r"^```(?:json)?\n(.*)\n```$", body, re.DOTALL)
    if m:
        return m.group(1)
    return body

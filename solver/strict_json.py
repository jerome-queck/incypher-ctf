"""One lossless UTF-8 JSON-object decoding seam."""

from __future__ import annotations

import json
from typing import Any


class StrictJSONError(ValueError):
    pass


def strict_json_value(body: bytes, *, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise StrictJSONError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StrictJSONError(f"{label} is not valid UTF-8 JSON: {error}") from None


def strict_json_object(body: bytes, *, label: str) -> dict[str, Any]:
    value = strict_json_value(body, label=label)
    if not isinstance(value, dict):
        raise StrictJSONError(f"{label} is not a JSON object")
    return value

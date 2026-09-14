"""Canonical names and endpoints for Board-profile evidence documents."""

from __future__ import annotations

import re

from solver.board import READ_CONTRACT_CONTROL

PROFILE_ENDPOINTS = {
    "identity": "/api/v1/users/me",
    "landing": "/",
    "read_contract": READ_CONTRACT_CONTROL,
    "challenges": "/api/v1/challenges",
    "ledger": "/plugins/ctfd-chall-manager/instances",
    "mana": "/api/v1/plugins/ctfd-chall-manager/mana",
    "configs": "/api/v1/configs",
    "anonymous_challenges": "/api/v1/challenges",
}
_DETAIL_NAME = re.compile(r"challenge_detail_([1-9][0-9]*)")
_DETAIL_ENDPOINT = re.compile(r"/api/v1/challenges/([1-9][0-9]*)")


def challenge_detail_name(endpoint: str) -> str:
    """Name one positive-ID detail response from its exact endpoint."""

    match = _DETAIL_ENDPOINT.fullmatch(endpoint)
    if match is None:
        raise ValueError("Board-profile Challenge detail endpoint is invalid")
    return f"challenge_detail_{match.group(1)}"


def profile_document_endpoint(name: str) -> str:
    """Return the only endpoint allowed for one canonical observation name."""

    if name in PROFILE_ENDPOINTS:
        return PROFILE_ENDPOINTS[name]
    match = _DETAIL_NAME.fullmatch(name)
    if match is None:
        raise ValueError("Board-profile document name is invalid")
    return f"/api/v1/challenges/{match.group(1)}"


def profile_document_sort_key(name: str) -> tuple[int, int]:
    """Keep fixed controls first and variable Challenge details in numeric ID order."""

    if name in PROFILE_ENDPOINTS:
        return tuple(PROFILE_ENDPOINTS).index(name), 0
    match = _DETAIL_NAME.fullmatch(name)
    if match is None:
        raise ValueError("Board-profile document name is invalid")
    return len(PROFILE_ENDPOINTS), int(match.group(1))


def is_profile_document_name(name: object) -> bool:
    return isinstance(name, str) and (name in PROFILE_ENDPOINTS or _DETAIL_NAME.fullmatch(name) is not None)


__all__ = [
    "PROFILE_ENDPOINTS",
    "challenge_detail_name",
    "is_profile_document_name",
    "profile_document_endpoint",
    "profile_document_sort_key",
]

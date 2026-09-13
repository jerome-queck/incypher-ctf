"""Resolve submission identity inputs from authenticated canonical projections."""

from __future__ import annotations

from collections.abc import Mapping

from solver.candidate_admission_contracts import SubmissionContext
from solver.instance_ledger import EMPTY, POPULATED, LedgerResult


class SubmissionContextUnavailable(ValueError):
    pass


class SubmissionContextResolver:
    def __init__(self, *, board_identity: str, revisions: Mapping[int, str], instance_ledger: LedgerResult):
        if not board_identity:
            raise SubmissionContextUnavailable("Board identity is unsettled")
        self._board_identity = board_identity
        self._revisions = dict(revisions)
        self._ledger = instance_ledger

    def resolve(self, challenge_id: int, *, requires_instance: bool) -> SubmissionContext:
        revision = self._revisions.get(challenge_id)
        if not revision:
            raise SubmissionContextUnavailable("Challenge revision is absent")
        if not requires_instance:
            return SubmissionContext.static(revision, self._board_identity)
        if self._ledger.outcome not in {EMPTY, POPULATED}:
            raise SubmissionContextUnavailable("authenticated Instance ledger is unsettled")
        row = next((row for row in self._ledger.owned if int(row.challenge_id) == challenge_id), None)
        if row is None or not row.row_id or not row.response_digest:
            raise SubmissionContextUnavailable("authenticated Instance row is absent")
        return SubmissionContext.authenticated_instance(revision, row.row_id, row.response_digest)

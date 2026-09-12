"""Proof that only the post-bootstrap Codex owner retains native subscription custody."""

from __future__ import annotations

import os

from solver.broker_contracts import Broker, BrokerReceipt
from solver.codex_control import CodexControl
from solver.executor_secret_probe import ExecutorProbeResult


def establish_service_custody(
    control: CodexControl,
    receipt: BrokerReceipt,
    probe: ExecutorProbeResult,
) -> None:
    """Accept custody only in the transferred Codex owner, after hostile probes are clear."""

    if receipt.owner is not Broker.CODEX or receipt.pid != os.getpid() or not receipt.secret_names:
        raise PermissionError("native subscription custody does not belong to Codex Control")
    control.record_custody(receipt)
    control.record_attempt_probe(probe)


__all__ = ["establish_service_custody"]

"""The Run-controller Boot, its refusals, and the exit code the Supervisor reads.

`python3 -m solver` remains the v1 execution path behind v2's PID-1 Supervisor. The child keeps the
existing Board and Attempt composition while the Supervisor owns Boot identity, signals and process
lifecycle.

Everything below the loop is handed its collaborators, so this is the only file that reads the
environment, opens files, or decides what talks to what. It is also the only place a `Refusal`
becomes an exit code: the whole point of refusing is that it happens at 10:15 with a human standing
there, so it exits with a sentence naming the fact that was missing rather than raising a traceback
at nobody.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Mapping
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from solver import boot, profile
from solver.clock import Clock, SystemClock, qualification_from_environment
from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import RuntimeBinding
from solver.attempt_executor_pool import ATTEMPT_UID, POOL_ENV, attach_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.board import Board
from solver.capability import CapabilityAuthority, PeerIdentity
from solver.board_broker import BoardCompatibilityClient, BoardProfileClient
from solver.coherent_intake import BrokerIntakeSource, CoherentIntake, contract_from_profile_receipt
from solver.board_broker_contracts import BOARD_BROKER_SOCKET_ENV, BOARD_PROFILE_HANDLE_ENV
from solver.boot import Refusal
from solver.codex import Invocation, asking
from solver.codex_control_contracts import CODEX_CONTROL_SOCKET_ENV
from solver.event_store import EventStoreDamage
from solver.event_store_storage import atomic_write
from solver.cpa_contracts import CPAConfig
from solver.cpa_responses import CPAResponsesModel
from solver.cpa_service import CPA_CREDENTIAL_FD_ENV, CPA_RESPONSES_URL_ENV, CPALeadPort, CPAService
from solver.flag import Flags, Pace
from solver.final_interval import FinalIntervalController
from solver.final_interval_profile import verify_selected_profile
from solver.final_candidate_queue import FinalCandidateQueue
from solver.lane_topology import LaneController
from solver.lane_topology_contracts import LaneProfile, OwnerTermination
from solver.instance import INSTANCED_ELSEWHERE, INSTANCED_TYPE, Instances
from solver.instance_ledger import AuthenticatedIdentity, read_profiled_instance_ledger
from solver.instance_ledger import write_receipt as write_instance_ledger_receipt
from solver.instance_lease_contracts import LeasePhase, LeaseVerdict, RowCorroboration
from solver.instance_lease_archive import persist_reconciled_close, replay_leases_across_runs
from solver.instance_reconciliation import InstanceReconciler
from solver.instance_reconciliation_contracts import AdmissionVerdict
from solver.instance_reconciliation_contracts import BootOwnership
from solver.instance_reconciliation_receipt import link_manifest as link_reconciliation_manifest
from solver.instance_reconciliation_receipt import write_receipt as write_reconciliation_receipt
from solver.event_store_contracts import GenerationAuthority, GenerationDisposition
from solver.intake import Intake
from solver.intake_qualification import NoCoherentSnapshot
from solver.isolation import IMAGE_ID as STRICT_IMAGE_ENV
from solver.isolation_receipt import RECEIPT_FILENAME as ISOLATION_RECEIPT_FILENAME
from solver.record import Recorder
from solver.redaction import Redactor
from solver.replay import verify_and_materialize_run_state
from solver.run import WORK_ROOT, Ending, Run, Steps
from solver.schedule import Dials, Scheduler, Window
from solver.order_runtime import CanonicalScheduler
from solver.lead_controller import LeadController
from solver.lead_v1_adapter import V1LeadAdapter
from solver.tool_control import AttemptToolRuntime, ToolController, attempt_components
from solver.candidate_admission import CandidateAdmission
from solver.submission.ambiguity_types import CompleteSubmissionIdentity
from solver.submission.bridge import CandidateSubmissionBridge, ObservedCandidateSubmissionBridge
from solver.submission.context import SubmissionContextResolver
from solver.submission.epoch import SubmissionEpochAuthority
from solver.submission.runtime import compose_submission_runtime
from solver.submission.authority import (
    ACCOUNT_POST_INTERVAL_SECONDS,
    SUBMISSION_REQUEST_DEADLINE_SECONDS,
    SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
)
from solver.submission.receipt import link_manifest as link_submission_manifest
from solver.submission.receipt import write_receipt as write_submission_receipt
from solver.write_reservation import Capacity, EffectIdentity
from solver.write_reservation_contracts import Pool, RetentionPolicy

# Where **Run state** goes: ADR-0008's one writable path, host-mounted, holding what a Run produces
# and nothing it reads. Not `state` bare — that reads as the Solver's in-memory state, which is a
# different thing and survives nothing (`CONTEXT.md`, *Run state*).
RUN_STATE = Path("/state")


def _active_initial_leases(coordinator) -> dict[object, object]:
    return {grant.challenge_id: grant for grant in coordinator.leases() if grant.phase is not LeasePhase.CLOSED}


def _submission_identity_composition(intake, ledger, board_identity, store, timestamp):
    """Bind Candidate submission identity to this Boot's canonical Board projections."""
    challenges = intake.order_authority().snapshot.challenges
    revisions = {int(item.challenge_id.value): item.revision_digest for item in challenges}
    instance_required = {
        int(item.challenge_id.value): item.challenge_type == INSTANCED_TYPE
        or item.challenge_type in INSTANCED_ELSEWHERE
        for item in challenges
    }
    contexts = SubmissionContextResolver(
        board_identity=board_identity,
        revisions=revisions,
        instance_ledger=ledger,
    )
    epochs = SubmissionEpochAuthority(store, timestamp)
    epochs.ensure(board_identity)

    def context_for(challenge_id):
        return contexts.resolve(challenge_id, requires_instance=instance_required[challenge_id])

    def identity_for(candidate):
        context = candidate.submission_context
        if context != context_for(candidate.challenge_id):
            raise ValueError("Candidate submission context is not the current canonical projection")
        return CompleteSubmissionIdentity(
            board_identity,
            candidate.challenge_id,
            context.challenge_revision,
            context.instance_provenance,
            candidate.candidate_digest,
            epochs.current(board_identity),
        )

    return context_for, identity_for, epochs


# The two directories this file names under the mount it was pointed at, and they are named the
# same way on purpose: a Solver handed a different `/state` is handed a different one whole, and a
# path that stayed absolute while its sibling followed the mount is the seam only this file can see.
#
# The judge is spawned in its own rather than in an Attempt's or in the Run's record: the CLI runs
# *in* its working directory, and either of those would stand a judgement among a Challenge's files
# or this Run's own stream. What that buys, and what a read-only sandbox still allows, is
# `solver/codex.py`, at `asking`. The other is `run.WORK_ROOT` re-rooted rather than restated, so
# the model's directories cannot drift from the module that lays them out (ADR-0025 namespaces the
# event beneath it).
JUDGE_WORKDIR = "triage"
ATTEMPT_WORKDIRS = WORK_ROOT.name

# What a Run exits with, because a supervisor at v2 and a human at 16:05 read the same number.
CLEAN = 0
BROKEN = 1
REFUSED = 2

# The run-close cause of a Run that opened its window and then refused. It is written rather than
# left off, because a stream that stops after `run-open` is indistinguishable from a container that
# was killed, and those want opposite investigations.
REFUSED_AT_BOOT = "refused-at-boot"

# Tests may inject the retired direct transport explicitly. Production never assigns this seam.
TEST_DIRECT_BOARD_FACTORY: Callable[[str, str], Board] | None = None
CPA_ROUTE_ENV = "INCYPHER_INFERENCE_ROUTE"
CPA_ROUTE = "private-cpa"
LANES_ENV = "INCYPHER_LANES"
CANDIDATE_MANIFEST_ENV = "INCYPHER_CANDIDATE_MANIFEST"
CANDIDATE_SIGNATURE_ENV = "INCYPHER_CANDIDATE_MANIFEST_SIGNATURE"
EVALUATOR_PUBLIC_KEY_ENV = "INCYPHER_EVALUATOR_PUBLIC_KEY"
FINAL_INTERVAL_PROFILE_ENV = "INCYPHER_FINAL_INTERVAL_PROFILE"
FINAL_INTERVAL_PROFILE_SIGNATURE_ENV = "INCYPHER_FINAL_INTERVAL_PROFILE_SIGNATURE"
TRUSTED_EVALUATOR_KEY_DIGEST = (  # gitleaks:allow
    "672b8a4f435628a41eb590ee563c9e395cbebfbc766accf20efc2e028d302c5b"
)


def _selected_candidate_profile(environ: Mapping[str, str]):
    """Verify the host-selected exact Candidate before its profile controls Boot."""
    paths = tuple(
        environ.get(name, "") for name in (CANDIDATE_MANIFEST_ENV, CANDIDATE_SIGNATURE_ENV, EVALUATOR_PUBLIC_KEY_ENV)
    )
    if not all(paths):
        if TEST_DIRECT_BOARD_FACTORY is not None or not environ.get(BOARD_BROKER_SOCKET_ENV):
            # An absent broker is refused at its older, more specific pre-effect boundary.
            return None
        raise Refusal(f"{boot.MARK} signed release-candidate profile is required")
    manifest_path, signature_path, public_path = map(Path, paths)
    try:
        public = public_path.read_bytes()
        if hashlib.sha256(public).hexdigest() != TRUSTED_EVALUATOR_KEY_DIGEST:
            raise ValueError("untrusted evaluator key")
        verified = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(public_path),
                "-in",
                str(manifest_path),
                "-sigfile",
                str(signature_path),
            ],
            capture_output=True,
            check=False,
        )
        if verified.returncode:
            raise ValueError("invalid evaluator signature")
        from solver.manifest import canonical_manifest_bytes, parse_manifest

        manifest = parse_manifest(manifest_path.read_bytes())
        if manifest_path.read_bytes() != canonical_manifest_bytes(manifest) + b"\n":
            raise ValueError("manifest is not canonical")
        expected_image = environ.get("INCYPHER_IMAGE_MANIFEST", "") or environ.get(STRICT_IMAGE_ENV, "")
        if expected_image and manifest["candidate"]["image_digest"] != expected_image:
            raise ValueError("manifest names another exact image")
        row_paths = tuple(
            environ.get(name, "") for name in (FINAL_INTERVAL_PROFILE_ENV, FINAL_INTERVAL_PROFILE_SIGNATURE_ENV)
        )
        if not all(row_paths):
            raise ValueError("signed final-interval selected profile is required")
        final_profile = verify_selected_profile(
            Path(row_paths[0]),
            Path(row_paths[1]),
            public_path,
            manifest,
            trusted_key_digest=TRUSTED_EVALUATOR_KEY_DIGEST,
            expected_image_digest=expected_image,
        )
        return manifest["selected_profile"], final_profile
    except (OSError, ValueError) as error:
        raise Refusal(f"{boot.MARK} signed release-candidate profile is invalid — {error}") from None


def _deny_cpa_tool(_name, _arguments):
    raise PermissionError("production CPA Tool profile admits proposal tools only")


def main(environ: Mapping[str, str], *, run_state: Path = RUN_STATE, boards: Path = profile.BOARDS) -> int:
    """Boot, run, and answer with the exit code. The only function in this repository that prints."""
    try:
        ending = _run(environ, run_state=run_state, boards=boards)
    except Refusal as refused:
        print(refused, file=sys.stderr, flush=True)
        return REFUSED
    print(
        f"{ending.cause}: {ending.attempts} attempt(s), {len(ending.flags)} flag(s)"
        + (f", still held: {', '.join(ending.left_held)}" if ending.left_held else "")
        + (f", not swept: {ending.unswept}" if ending.unswept else "")
        + (f" — {ending.detail}" if ending.detail else ""),
        flush=True,
    )
    return CLEAN if ending.clean else BROKEN


def _run(environ: Mapping[str, str], *, run_state: Path, boards: Path) -> Ending:
    """Every refusal, then the Run — in the order that puts each check before the thing it guards.

    The order is the design. Credentials come first because they cost no network call; the tracked
    profile comes next because it decides whether we are even pointed at a Board we hold rules for;
    the read contract comes before anything is believed off the wire; and the first Intake comes last
    because it is the most expensive and the only one that needs a Recorder to write to.
    """
    held = boot.setup(environ)
    try:
        qualification = qualification_from_environment(environ)
    except ValueError as error:
        raise Refusal(f"{boot.MARK} qualification clock refused — {error}") from None
    clock = qualification.clock if qualification is not None else SystemClock()
    rules = qualification.rules if qualification is not None else profile.rules_for(held.url, boards)
    selected = _selected_candidate_profile(environ)
    selected_candidate_profile, final_profile = selected if selected is not None else (None, None)
    try:
        verify_and_materialize_run_state(run_state, held.run_id, Redactor.for_declared_secrets(environ))
    except EventStoreDamage as damage:
        raise Refusal(f"{boot.MARK} canonical state verification refused this Run — {damage.classification}") from None
    except (OSError, ValueError) as unusable:
        raise Refusal(f"{boot.MARK} {run_state} is not usable as this Run's state — {unusable}") from None
    return _run_admitted(
        environ,
        run_state=run_state,
        held=held,
        rules=rules,
        rules_source=str(Path(boards) / f"{rules.event}{profile.SUFFIX}"),
        profile_handle=environ.get(BOARD_PROFILE_HANDLE_ENV, ""),
        board_broker_path=Path(environ[BOARD_BROKER_SOCKET_ENV]) if environ.get(BOARD_BROKER_SOCKET_ENV) else None,
        boot_id=environ.get("SUPERVISOR_BOOT_ID", ""),
        selected_candidate_profile=selected_candidate_profile,
        selected_final_profile=final_profile,
        clock=clock,
        qualification_seed=qualification.seed if qualification is not None else "",
    )


def _run_admitted(
    environ: Mapping[str, str],
    *,
    run_state: Path,
    held: boot.Setup,
    rules: profile.Rules,
    rules_source: str = "",
    profile_handle: str = "",
    board_broker_path: Path | None = None,
    boot_id: str = "",
    selected_candidate_profile=None,
    selected_final_profile=None,
    clock: Clock | None = None,
    qualification_seed: str = "",
) -> Ending:
    """Keep capability IPC live beside every admitted v1 Board and inference call."""

    held.must_hold(rules.requires)
    if board_broker_path is None:
        if TEST_DIRECT_BOARD_FACTORY is None:
            raise Refusal(f"{boot.MARK} qualified Board broker is required for production Intake")
        board = TEST_DIRECT_BOARD_FACTORY(held.url, held.token)
        discovered = profile.discovered(board, TEST_DIRECT_BOARD_FACTORY(held.url, ""), rules)
    else:
        profiler = BoardProfileClient(board_broker_path, profile_handle)
        decision = profiler.qualify(rules, rules_source or f"{rules.event}{profile.SUFFIX}")
        if not decision.authoritative or decision.profile is None:
            raise Refusal(f"{boot.MARK} Board profile is incompatible — {decision.reason}")
        profiler.open_operations()
        discovered = decision.profile
        board = BoardCompatibilityClient(board_broker_path)

    clock = clock or SystemClock()
    now = clock.now()
    try:
        write_profile = None
        if selected_candidate_profile is not None:
            from solver.storage_governor_contracts import StorageGovernorProfile

            write_profile = StorageGovernorProfile.from_release_candidate(
                selected_candidate_profile["storage"]
            ).write_profile()
        recorder = Recorder(
            run_state,
            held.run_id,
            Redactor.for_declared_secrets(environ),
            **({"write_profile": write_profile} if write_profile is not None else {}),
        )
        from solver.recovery.contracts import FaultKind
        from solver.recovery.runtime import DeterministicRecovery, RecoveryRegistry

        deterministic_recovery = DeterministicRecovery(
            run_state,
            held.run_id,
            recorder.redactor,
            now=clock.now,
            authority=recorder.write_authority,
        )
        deterministic_recovery.validate_boot_adapters()
        window = Window.opened(
            recorder.run_dir,
            lasting=boot.lasting(rules.closes_at, rules.window_seconds, held.run_seconds, now),
            now=now,
        )
    except (OSError, ValueError) as unusable:
        # The mount is the one thing outside the image a Run depends on, and Colima mounts `$HOME`
        # and nothing else — a `-v` from outside it hands the container an empty directory in
        # silence. A window already there that cannot be read is refused for the same reason it is
        # never replaced: writing a fresh one over it is the silent extension the stamp prevents.
        raise Refusal(f"{boot.MARK} {run_state} is not usable as this Run's state — {unusable}") from None
    try:
        selected_lanes = int(
            selected_candidate_profile["lanes"]
            if selected_candidate_profile is not None
            else environ.get(LANES_ENV, "1")
        )
    except (KeyError, TypeError, ValueError):
        raise Refusal(f"{boot.MARK} selected profile must declare one or two Lanes") from None
    if selected_lanes not in {1, 2}:
        raise Refusal(f"{boot.MARK} {LANES_ENV} must select one or two Lanes")
    dials = Dials(
        concurrency=selected_lanes,
        tail_seconds=(
            int(selected_final_profile["final_submission_reserve_seconds"])
            if selected_final_profile is not None
            else Dials().tail_seconds
        ),
        floor_seconds=(
            int(selected_final_profile["attempt_floor_seconds"])
            if selected_final_profile is not None
            else Dials().floor_seconds
        ),
    )
    if selected_final_profile is not None and int(selected_final_profile["window_seconds"]) != rules.window_seconds:
        raise Refusal(f"{boot.MARK} signed final-interval window disagrees with Board rules")
    post_interval_seconds = max(
        float(
            selected_final_profile["account_post_interval_seconds"]
            if selected_final_profile is not None
            else ACCOUNT_POST_INTERVAL_SECONDS
        ),
        60.0 / max(1, discovered.submissions_per_minute),
    )
    request_deadline_seconds = float(
        selected_final_profile["submission_request_deadline_seconds"]
        if selected_final_profile is not None
        else SUBMISSION_REQUEST_DEADLINE_SECONDS
    )
    uncertainty_margin_seconds = float(
        selected_final_profile["submission_uncertainty_margin_seconds"]
        if selected_final_profile is not None
        else SUBMISSION_UNCERTAINTY_MARGIN_SECONDS
    )

    maximum = (
        selected_candidate_profile["storage"]["maximum_authority_effect_reservation"]
        if selected_candidate_profile is not None
        else None
    )
    final_need = (
        Capacity(
            int(maximum["bytes"]),
            int(maximum["filesystem_objects"]),
            sum(int(value) for value in maximum["operations"].values()),
            **{
                name: int(maximum["operations"][name])
                for name in ("create", "append", "rename", "unlink", "durability")
            },
        )
        if maximum is not None
        else Capacity(16_384, 1, 12)
    )

    def reserve_final_authority(identity: str):
        return recorder.write_authority.reserve(
            f"final-interval:{identity}",
            EffectIdentity("final-interval.authority", identity),
            final_need,
            retention=RetentionPolicy.RELEASE,
            retry_aborted=True,
        )

    def reserve_terminal_authority(identity: str):
        return recorder.write_authority.reserve(
            f"final-interval:{identity}",
            EffectIdentity("final-interval.authority", identity),
            final_need,
            pool=Pool.TERMINAL,
            retention=RetentionPolicy.RECEIPT,
            retry_aborted=True,
        )

    final_interval = FinalIntervalController(
        state=run_state,
        run_id=held.run_id,
        opened_at=window.opened_at,
        ends_at=window.ends_at,
        final_submission_reserve_seconds=int(dials.tail_seconds),
        attempt_floor_seconds=int(dials.floor_seconds),
        enabled_lanes=tuple(f"lane-{index}" for index in range(1, dials.concurrency + 1)),
        now=clock.now,
        reserve=reserve_final_authority,
        account_post_interval_seconds=post_interval_seconds,
        submission_request_deadline_seconds=request_deadline_seconds,
        submission_uncertainty_margin_seconds=uncertainty_margin_seconds,
        reserve_terminal=reserve_terminal_authority,
        event_store=recorder.event_store,
        write_authority=recorder.write_authority,
    )
    resources = selected_candidate_profile["resources"] if selected_candidate_profile is not None else None
    lane_owner: dict[str, object] = {}

    def terminate_lane_owner(binding):
        owner = lane_owner.get("run")
        if owner is None:
            return OwnerTermination(False, hashlib.sha256(f"unbound:{binding.attempt_id}".encode()).hexdigest())
        return owner.terminate_lane_owner(binding, cleanup_seconds=10.0)

    lane_controller = (
        LaneController(
            state=run_state,
            run_id=held.run_id,
            profile=LaneProfile(
                lanes=selected_lanes,
                global_resource_units=selected_lanes,
                global_cpu_quota_us=int(resources["cpu_limit"]) if resources else 100_000 * selected_lanes,
                global_memory_bytes=int(resources["memory_bytes"]) if resources else 2 * 1024**3 * selected_lanes,
                global_pids=int(resources["pid_limit"]) if resources else 256 * selected_lanes,
                global_filesystem_bytes=2 * 1024**3 * selected_lanes,
                global_wall_seconds=max(1, int((window.ends_at - now).total_seconds())),
            ),
            generations=recorder.generations,
            timestamp=lambda: clock.now().isoformat(),
            terminate=terminate_lane_owner,
            final_interval=final_interval,
        )
        if selected_lanes == 2
        else None
    )
    recorder.run_open(
        board_profile={
            **discovered.recorded(),
            "run": {
                **held.recorded(),
                "restarted": window.restarted,
                "opened_at": window.opened_at.isoformat(),
                "ends_at": window.ends_at.isoformat(),
            },
            "dials": asdict(dials),
        }
    )

    if board_broker_path is None:
        intake = Intake(board, recorder)
    else:
        intake_contract = contract_from_profile_receipt(run_state, held.run_id)
        intake = CoherentIntake(
            BrokerIntakeSource(
                board_broker_path,
                state=run_state,
                run_id=held.run_id,
                boot_id=boot_id,
                board_url=held.url,
                profile_handle=profile_handle,
                contract=intake_contract,
                generations=recorder.generations,
            ),
            recorder,
            intake_contract,
            Redactor.for_declared_secrets(environ),
        )
    opening = intake.sync()
    if not isinstance(opening, NoCoherentSnapshot) and hasattr(opening, "believable") and not opening.believable:
        # The same fault mid-Run keeps the last snapshot and carries on — a Board that cannot be
        # read is not a Board that emptied. At boot there is no last snapshot to keep, and a Run that
        # started here would spend its window ranking nothing while reporting success.
        recorder.run_close(cause=f"{REFUSED_AT_BOOT} — {opening.outcome}")
        raise Refusal(f"{boot.MARK} the first Intake did not believe the Board — {opening.detail}")

    steps = Steps()
    ledger_identity = None
    ledger_broker = None
    ledger = None
    if board_broker_path is not None:
        ledger_identity = AuthenticatedIdentity(
            discovered.instance_ledger_mode,
            discovered.authenticated_user_id,
            discovered.authenticated_team_id,
        )
        ledger_broker = board
    lease_coordinator = None
    lease_target_authority = None
    qualification_generation = None
    if board_broker_path is not None:
        from solver.instance_lease import LeaseCoordinator

        owner_id = (
            f"team:{discovered.authenticated_team_id}"
            if discovered.instance_ledger_mode == "teams"
            else f"user:{discovered.authenticated_user_id}"
        )
        lease_coordinator = LeaseCoordinator(
            recorder.write_authority,
            board,
            run_id=held.run_id,
            board_id=held.url,
            owner_id=owner_id,
            corroborate=lambda challenge_id: _owned_instance_row(ledger_identity, ledger_broker, challenge_id),
            admit_generation=lambda generation_id, effect: _admit_lease_effect(recorder, generation_id, effect),
            generation_events=recorder.event_store.events,
        )
        from solver.lease_target import LeaseTargetAuthority

        lease_target_authority = LeaseTargetAuthority(
            lease_coordinator,
            recorder.generations,
            run_id=held.run_id,
            board_id=held.url,
            owner_id=owner_id,
        )
        ledger = read_profiled_instance_ledger(ledger_identity, ledger_broker)
        ledger_receipt = write_instance_ledger_receipt(run_state, held.run_id, ledger)
        generations = recorder.generations.projection().generations
        generations = _reconcile_interrupted_attempts(recorder, generations)
        ownership = _boot_ownership(recorder.event_store.events(), boot_id, generations)
        instance_reconciler = InstanceReconciler(
            recorder.write_authority,
            run_id=held.run_id,
            board_id=held.url,
            cleanup=lambda grant, snapshot_id: _reconcile_lease_cleanup(
                lease_coordinator, grant, snapshot_id, run_state, held.run_id
            ),
            finalize=lambda grant, snapshot_id: persist_reconciled_close(run_state, grant, snapshot_id),
        )
        reconciliation = instance_reconciler.reconcile(
            boot_id=boot_id,
            ledger=ledger,
            leases=replay_leases_across_runs(run_state, held.url),
            generations=generations,
            ownership=ownership,
        )
        reconciliation_receipt = write_reconciliation_receipt(
            reconciliation,
            recorder.write_authority,
            run_state / "runs" / held.run_id / "canonical" / "instance-reconciliation.receipt.json",
        )
        from solver.recovery.instance import INSTANCE_ADAPTER, InstanceObservation, instance_recovery

        recovery_cycle = [0]

        def observe_instance_authority():
            recovery_cycle[0] += 1
            current_ledger = read_profiled_instance_ledger(ledger_identity, ledger_broker)
            current_generations = recorder.generations.projection().generations
            current_ownership = _boot_ownership(recorder.event_store.events(), boot_id, current_generations)
            current_leases = replay_leases_across_runs(run_state, held.url)
            join_digest = instance_reconciler.project_join(
                current_ledger, current_leases, current_generations, current_ownership
            )

            def reconcile_observation():
                nonlocal reconciliation, reconciliation_receipt, ledger_receipt
                reconciliation = instance_reconciler.reconcile(
                    boot_id=boot_id,
                    cycle_id=f"{boot_id}:recovery-{recovery_cycle[0]}",
                    ledger=current_ledger,
                    leases=current_leases,
                    generations=current_generations,
                    ownership=current_ownership,
                )
                ledger_receipt = write_instance_ledger_receipt(run_state, held.run_id, current_ledger)
                reconciliation_receipt = write_reconciliation_receipt(
                    reconciliation,
                    recorder.write_authority,
                    run_state / "runs" / held.run_id / "canonical" / "instance-reconciliation.receipt.json",
                )
                return reconciliation

            return InstanceObservation(
                join_digest,
                reconcile_observation,
                str(
                    {
                        "ledger": current_ledger.identity_digest,
                        "leases": len(current_leases),
                        "generations": len(current_generations),
                        "ownership": current_ownership.document(),
                    }
                ).encode(),
                probation=lambda: instance_reconciler.probation(join_digest, boot_id=boot_id),
            )

        registry = RecoveryRegistry()
        registry.register(
            INSTANCE_ADAPTER,
            lambda config: instance_recovery(config["failed_join_digest"], observe_instance_authority),
        )

        def complete_instance_recovery(result):
            receipt = json.loads(result.receipt_path.read_text())
            deadline = dt.datetime.fromisoformat(receipt["original_deadline"])
            while not receipt["final_outcome"] and window.left(clock.now()) > 0:
                remaining = min((deadline - clock.now()).total_seconds(), window.left(clock.now()))
                if remaining > 0:
                    clock.sleep(min(15.0, remaining))
                replayed = deterministic_recovery.replay(registry)
                if not replayed:
                    break
                receipt = json.loads(replayed[-1].receipt_path.read_text())
            return receipt

        for replayed_incident in deterministic_recovery.replay(registry):
            complete_instance_recovery(replayed_incident)

        if reconciliation.verdict is not AdmissionVerdict.OPEN:
            recovery_deadline = min(clock.now() + dt.timedelta(seconds=180), window.ends_at)
            result = deterministic_recovery.handle(
                kind=FaultKind.INSTANCE,
                fault_id=f"{boot_id}:instance-reconciliation",
                scope="external:instance",
                generation_id="all-active",
                evidence=";".join(reconciliation.unsettled),
                failed_action_value=reconciliation.join_digest,
                original_deadline=recovery_deadline,
                recovery=instance_recovery(reconciliation.join_digest, observe_instance_authority),
            )
            complete_instance_recovery(result)
        if reconciliation.verdict is not AdmissionVerdict.OPEN:
            recorder.write_authority.close()
            raise Refusal(f"{boot.MARK} Instance authority remains contained; Work admission is closed")
    instances = Instances(
        board,
        recorder,
        step_numbers=steps.spend,
        ledger_identity=ledger_identity,
        ledger_broker=ledger_broker,
        coordinator=lease_coordinator,
        target_authority=lease_target_authority,
    )
    # Triage's last resort, and the one collaborator only this file can hand it: what the Board
    # states and what its solves say are read off the Board itself, and the model is asked about
    # whatever neither of them could rank. Left at its default nothing is asked at all, and a Board
    # that publishes no difficulty is triaged entirely at the floor — every Challenge budgeted
    # alike, and every Tier recorded `unjudged` (ADR-0006). Brunner published one on nearly every
    # Challenge, which is why four gate Runs never showed this.
    #
    # It is handed the rung this Run leads with and no chain behind it: failing over would spend a
    # second invocation on a Tier, and a judge that answers nothing leaves the floor either way. And
    # it is handed the Board's `web_search` for the reason the Attempt below is: the tool is the
    # Board's to withdraw, and a Run whose judge kept it would be playing one invocation outside the
    # rules the rest of it obeys (ADR-0014).
    judge = asking(held.chain[0], recorder=recorder, workdir=run_state / JUDGE_WORKDIR, web_search=rules.web_search)
    attempt_executor = None
    tool_runtime = None
    if POOL_ENV in environ:
        binding = RuntimeBinding(
            image_id=environ.get(STRICT_IMAGE_ENV, ""),
            image_manifest_digest=environ.get("INCYPHER_IMAGE_MANIFEST", ""),
            image_config_digest=environ.get("INCYPHER_IMAGE_CONFIG", ""),
            platform=environ.get("INCYPHER_IMAGE_PLATFORM", ""),
        )
        attempt_executor = AttemptExecutor(
            state=run_state,
            run_id=held.run_id,
            isolation_receipt=(run_state / "runs" / held.run_id / "canonical" / ISOLATION_RECEIPT_FILENAME),
            binding=binding,
            generation_fence=recorder.generations,
            runtime=AttemptRuntime(attach_attempt_pool(environ)),
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        tool_peer = PeerIdentity(
            pid=os.getpid(),
            uid=ATTEMPT_UID,
            gid=ATTEMPT_UID,
            started=boot_id,
            cgroup="attempt-executor-pool",
        )
        tool_authority = CapabilityAuthority(
            state=run_state,
            run_id=held.run_id,
            boot_id=boot_id,
            redactor=Redactor.for_declared_secrets(environ),
            peer_identity=lambda _connection: tool_peer,
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        tool_controller = ToolController(
            state=run_state,
            run_id=held.run_id,
            authority=tool_authority,
            image_digest=binding.image_manifest_digest,
            components=attempt_components(Path("/opt/solver/tool-supply/inventory.json")),
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        tool_runtime = AttemptToolRuntime(
            controller=tool_controller,
            executor=attempt_executor,
            run_id=held.run_id,
            boot_id=boot_id,
            peer=tool_peer,
        )
    stack = ExitStack()
    candidate_sink = None
    observed_candidate_sink = None
    final_candidate_queue = None
    submission_receipt_inputs = None
    if board_broker_path is not None:
        admission = CandidateAdmission(
            run_state,
            held.run_id,
            Redactor.for_declared_secrets(environ),
            lambda: clock.now().isoformat(),
            rules.flag_wrappers,
            hashlib.sha256(b"candidate-vault-v1\0" + held.token.encode()).digest(),
            recorder.generations,
        )
        if qualification_seed:
            from solver.qualification_seed import seed_final_interval_generation

            already_seeded = any(
                generation.attempt_id == "qualification-attempt-occupied"
                for generation in recorder.generations.projection().generations
            )
            if not already_seeded:
                qualification_generation = seed_final_interval_generation(
                    recorder,
                    selected_profile=selected_candidate_profile,
                )
        context_for, identity_for, submission_epochs = _submission_identity_composition(
            intake,
            ledger,
            held.url,
            recorder.event_store,
            lambda: clock.now().isoformat(),
        )
        submission_runtime = compose_submission_runtime(
            state=run_state,
            recorder=recorder,
            run_id=held.run_id,
            boot_id=boot_id,
            board_broker_path=board_broker_path,
            timestamp=lambda: clock.now().isoformat(),
            identity_for=identity_for,
            epoch_authority=submission_epochs,
            board_identity=held.url,
            monotonic=clock.monotonic,
            wall_time=clock.wall_time,
            sleep=clock.sleep,
            post_interval_seconds=post_interval_seconds,
            request_deadline_seconds=request_deadline_seconds,
            uncertainty_margin_seconds=uncertainty_margin_seconds,
            recovery=deterministic_recovery,
        )
        submission = submission_runtime.submission
        stack.callback(submission_runtime.close)
        candidate_sink = CandidateSubmissionBridge(admission, submission, context_for)
        observed_candidate_sink = ObservedCandidateSubmissionBridge(
            admission,
            submission,
            run_id=held.run_id,
            boot_id=boot_id,
            context_for=context_for,
            lane_for_attempt=lane_controller.lane_for_attempt if lane_controller is not None else None,
        )
        final_interval_generation = recorder.generations.acquire("final-interval", f"final-interval:{boot_id}")
        final_candidate_queue = FinalCandidateQueue(
            admission,
            submission,
            run_id=held.run_id,
            boot_id=boot_id,
            generation_id=final_interval_generation.generation_id,
        )
        if qualification_seed and qualification_generation is not None:
            from solver.qualification_seed import seed_final_interval_candidates

            seed_final_interval_candidates(
                recorder,
                admission,
                context_for,
                qualification_generation,
            )
            final_candidate_queue.prepare_all()
        submission_receipt_inputs = (recorder.run_dir / "canonical", held.run_id, recorder.write_authority)
    lead_adapter = _compose_cpa_lead(environ, run_state, held, boot_id, recorder, stack, candidate_sink)
    run = Run(
        profile=discovered,
        recorder=recorder,
        intake=intake,
        scheduler=(
            CanonicalScheduler(
                window,
                recorder,
                intake.order_authority,
                dials=dials,
                judge=judge,
                final_interval=final_interval,
                final_lane_ids=final_interval.enabled_lanes,
                now=clock.now,
            )
            if isinstance(intake, CoherentIntake)
            else Scheduler(window, recorder, dials=dials, judge=judge, now=clock.now)
        ),
        flags=Flags(
            board,
            recorder,
            flag_wrappers=rules.flag_wrappers,
            instances=instances,
            pace=Pace(per_minute=discovered.submissions_per_minute),
            step_numbers=steps.spend,
            candidate_submission=observed_candidate_sink,
        ),
        instances=instances,
        steps=steps,
        chain=held.chain,
        invocation=Invocation(reasoning_effort=dials.reasoning_effort, web_search=rules.web_search),
        work_root=run_state / ATTEMPT_WORKDIRS,
        attempt_executor=attempt_executor,
        tool_runtime=tool_runtime,
        board_broker_path=board_broker_path,
        board_broker_boot_id=boot_id,
        codex_control_path=Path(environ[CODEX_CONTROL_SOCKET_ENV]) if environ.get(CODEX_CONTROL_SOCKET_ENV) else None,
        lead_adapter=lead_adapter,
        inference_route=environ.get(CPA_ROUTE_ENV, "native-codex"),
        lane_controller=lane_controller,
        final_interval=final_interval,
        final_candidate_preparation=observed_candidate_sink,
        final_candidate_queue=final_candidate_queue,
        initial_leases=(_active_initial_leases(lease_coordinator) if lease_coordinator is not None else None),
        recovery=deterministic_recovery,
        now=clock.now,
        sleep=clock.sleep,
    )
    lane_owner["run"] = run
    _on_signal(run)
    try:
        return run.work()
    finally:
        if submission_receipt_inputs is not None:
            submission_receipt = write_submission_receipt(*submission_receipt_inputs)
            manifest_path = recorder.run_dir / "canonical" / "candidate-manifest.json"
            if manifest_path.exists():
                from solver.manifest import canonical_manifest_bytes, parse_manifest

                linked = link_submission_manifest(
                    parse_manifest(manifest_path.read_bytes()), submission_receipt, recorder.write_authority
                )
                atomic_write(manifest_path, canonical_manifest_bytes(linked) + b"\n")
        if lease_coordinator is not None:
            canonical = run_state / "runs" / held.run_id / "canonical"
            lease_coordinator.write_receipt(
                canonical / "instance-lease.receipt.json",
                manifest_path=canonical / "candidate-manifest.json",
            )
            manifest_path = canonical / "candidate-manifest.json"
            if manifest_path.exists():
                from solver.manifest import canonical_manifest_bytes, parse_manifest

                linked = link_reconciliation_manifest(
                    parse_manifest(manifest_path.read_bytes()),
                    reconciliation_receipt,
                    recorder.write_authority,
                    recorder.event_store.events(),
                    ledger_receipt,
                )
                atomic_write(manifest_path, canonical_manifest_bytes(linked) + b"\n")
        stack.close()
        if attempt_executor is not None:
            attempt_executor.close()


def _owned_instance_row(identity, broker, challenge_id):
    """Corroborate deploy identity from one authenticated, complete team ledger."""
    if identity is None or broker is None:
        return RowCorroboration(LeaseVerdict.UNCORROBORATED_ROW)
    result = read_profiled_instance_ledger(identity, broker)
    matches = [row.row_id for row in result.owned if row.challenge_id == challenge_id]
    if len(matches) == 1:
        return RowCorroboration(row_id=matches[0])
    foreign = any(row.challenge_id == challenge_id for row in result.foreign)
    return RowCorroboration(LeaseVerdict.FOREIGN_ROW if foreign else LeaseVerdict.UNCORROBORATED_ROW)


def _reconcile_lease_cleanup(coordinator, grant, snapshot_id, state, current_run_id):
    result = coordinator.reconcile_grant_release(grant, snapshot_id=snapshot_id)
    if grant.identity.run_id != current_run_id:
        result = persist_reconciled_close(state, result, snapshot_id)
    return result.phase is LeasePhase.CLOSED


def _boot_ownership(events, boot_id, generations):
    boot_opens = [
        event
        for event in events
        if event.payload.get("record") == "boot-open" and event.payload.get("boot_id") == boot_id
    ]
    prior_boots = {
        str(event.payload.get("boot_id"))
        for event in events
        if event.payload.get("record") == "boot-open" and event.payload.get("boot_id") != boot_id
    }
    closed_boots = {
        str(event.payload.get("boot_id")): event for event in events if event.payload.get("record") == "boot-close"
    }
    predecessor_proof = ()
    if len(boot_opens) == 1 and prior_boots <= closed_boots.keys():
        predecessor_proof = tuple(str(closed_boots[item].payload["event_id"]) for item in sorted(prior_boots))
    attempt_closes = {
        str(event.payload.get("attempt_id")): event
        for event in events
        if event.payload.get("record") == "attempt-close"
    }
    opened_attempts = {
        str(event.payload.get("attempt_id")) for event in events if event.payload.get("record") == "attempt-open"
    }
    interrupted = tuple(
        str(attempt_closes[generation.attempt_id].payload["event_id"])
        for generation in generations
        if generation.disposition is GenerationDisposition.INTERRUPT and generation.attempt_id in attempt_closes
    )
    all_attempts_closed = all(
        not generation.active
        and (generation.attempt_id not in opened_attempts or generation.attempt_id in attempt_closes)
        for generation in generations
    )
    return BootOwnership(
        predecessor_proof,
        interrupted,
        len(boot_opens) == 1 and all_attempts_closed,
        prior_boots <= closed_boots.keys(),
    )


def _reconcile_interrupted_attempts(recorder, generations):
    events = recorder.event_store.events()
    opened = {str(event.payload.get("attempt_id")) for event in events if event.payload.get("record") == "attempt-open"}
    closed = {
        str(event.payload.get("attempt_id")) for event in events if event.payload.get("record") == "attempt-close"
    }
    for generation in generations:
        if not generation.active:
            continue
        if generation.attempt_id not in opened:
            recorder.interrupt_generation(generation.generation_id)
            continue
        if generation.attempt_id not in closed:
            recorder.attempt_close(
                attempt_id=generation.attempt_id,
                cause="crashed",
                approach_label="reconciled-after-boot-loss",
                solves_at_close=0,
                extensions_granted=0,
                flag=None,
                generation_id=generation.generation_id,
            )
    return recorder.generations.projection().generations


def _admit_lease_effect(recorder, generation_id, effect):
    _decision, result = recorder.generations.authorize_and_commit(
        generation_id,
        GenerationAuthority.AUTHORITY,
        lambda grant: effect(grant.sequence, grant.event_id),
    )
    return result


def _compose_cpa_lead(environ, run_state, held, boot_id, recorder, stack, candidate_sink=None):
    credential_fd = environ.get(CPA_CREDENTIAL_FD_ENV, "")
    if not credential_fd:
        return None
    descriptor = int(credential_fd)
    try:
        credential = os.read(descriptor, 64 * 1024).decode()
    finally:
        os.close(descriptor)
    endpoint = environ.get(CPA_RESPONSES_URL_ENV, "")
    if not endpoint:
        raise Refusal(f"{boot.MARK} CPA custody is present but its Responses endpoint is absent")
    cpa_service = stack.enter_context(
        CPAService(
            endpoint=Path("/tmp/incypher-cpa") / held.run_id / boot_id / "cpa.sock",
            config=CPAConfig(max_turns=1, max_tools=0, allowed_tools=(), model=held.model),
            credential=credential,
            model=CPAResponsesModel(endpoint, held.model),
            execute_tool=_deny_cpa_tool,
            state=run_state,
            run_id=held.run_id,
            boot_id=boot_id,
            redactor=Redactor.for_declared_secrets(environ),
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
    )
    credential = ""
    lead = LeadController(
        run_state,
        held.run_id,
        Redactor.for_declared_secrets(environ),
        lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        CPALeadPort(cpa_service),
        fence=recorder.generations,
    )
    return V1LeadAdapter(lead, harness=CPA_ROUTE, route=CPA_ROUTE, candidate_sink=candidate_sink)


def _on_signal(run: Run) -> None:
    """Reach the reserved tail on `docker stop` rather than the ten-second grace and a SIGKILL.

    PID 1 is not handed the default action for a signal it has no handler for, so without this the
    Solver ignores SIGTERM entirely — and the Instances it holds are never reclaimed, because
    chall-manager does not evict.
    """
    for caught in (signal.SIGTERM, signal.SIGINT):
        signal.signal(caught, lambda _number, _frame: run.stop())


if __name__ == "__main__":
    sys.exit(main(os.environ))

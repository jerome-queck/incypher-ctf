"""Public qualification contract for one coherent Intake snapshot."""

import datetime as dt
import hashlib
import json
from dataclasses import replace

from solver.intake_qualification import (
    IntakeContract,
    IntakeDocument,
    IntakePass,
    IntakeProbe,
    NoCoherentSnapshot,
    PriorFence,
    SnapshotAvailable,
    availability,
    qualify,
)


PROFILE = "1" * 64
SUBJECT = "db187520f6abbe69fa69847b0f7167afbb6963a34d69647774f9f3f4291beb62"
CAPABILITY = "3" * 64
PEER = "4" * 64
NOW = dt.datetime(2026, 9, 22, 2, 30, tzinfo=dt.timezone.utc)
COVER = "files/1a1a1a/cover.png?token=signed-first"
COVER_RESIGNED = "files/1a1a1a/cover.png?token=signed-second"


def document(kind, body, *, pass_no, request, endpoint, page=0, challenge_id=None):
    raw = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
    return IntakeDocument(
        request_id=f"request:{pass_no}:{request}",
        classified_event_id=f"event:{pass_no}:{request}",
        pass_no=pass_no,
        kind=kind,
        endpoint=endpoint,
        status=200,
        content_type="application/json; charset=utf-8",
        raw=raw,
        original_bytes=len(raw),
        complete=True,
        profile_digest=PROFILE,
        subject_digest=SUBJECT,
        capability_digest=CAPABILITY,
        peer_digest=PEER,
        page=page,
        challenge_id=challenge_id,
    )


def attachment(pass_no, challenge_id, listing, body, *, outcome="answered", complete=True):
    observed = document(
        "attachment",
        body,
        pass_no=pass_no,
        request=f"attachment-{challenge_id}",
        endpoint=listing,
        challenge_id=challenge_id,
    )
    return IntakeDocument(
        **{
            **observed.__dict__,
            "content_type": "application/octet-stream",
            "outcome": outcome,
            "complete": complete,
        }
    )


def identity(pass_no, edge):
    return document(
        "identity",
        {"success": True, "data": {"id": 17, "team_id": None}},
        pass_no=pass_no,
        request=f"identity-{edge}",
        endpoint="/api/v1/users/me",
    )


def listed(challenge_id, *, value=500, solves=3):
    return {
        "id": challenge_id,
        "name": f"challenge-{challenge_id}",
        "category": "forensics",
        "type": "standard",
        "value": value,
        "solves": solves,
        "solved_by_me": False,
    }


def detail(challenge_id, *, description=None):
    return {
        "id": challenge_id,
        "description": description or f"statement-{challenge_id}",
        "attempts": 0,
        "max_attempts": None,
        "files": [],
    }


def intake_pass(pass_no, entries, *, total=None, pages=1, detail_overrides=None):
    total = len(entries) if total is None else total
    detail_overrides = detail_overrides or {}
    page = document(
        "list",
        {
            "success": True,
            "data": entries,
            "meta": {"pagination": {"page": 1, "pages": pages, "total": total, "next": None, "prev": None}},
        },
        pass_no=pass_no,
        request="list-1",
        endpoint="/api/v1/challenges?page=1",
        page=1,
    )
    details = tuple(
        document(
            "detail",
            {"success": True, "data": detail_overrides.get(entry["id"], detail(entry["id"]))},
            pass_no=pass_no,
            request=f"detail-{entry['id']}",
            endpoint=f"/api/v1/challenges/{entry['id']}",
            challenge_id=entry["id"],
        )
        for entry in entries
    )
    return IntakePass(pass_no, identity(pass_no, "before"), (page,), details, (), identity(pass_no, "after"))


def probe(first, second, *, prior=None, max_fetch_bytes=256 * 1024 * 1024, pagination_shape="ctfd-pages"):
    contract = IntakeContract(
        profile_digest=PROFILE,
        subject_digest=SUBJECT,
        max_fetch_bytes=max_fetch_bytes,
        pagination_shape=pagination_shape,
    )
    return IntakeProbe(
        "intake-attempt-000001",
        contract,
        PriorFence.genesis(PROFILE),
        (first, second),
        NOW,
        prior,
    )


def test_two_matching_complete_passes_publish_one_typed_replayable_snapshot():
    # Mixed IDs are deliberately admitted: the snapshot order must not rely on Python comparing
    # an int with a string.
    entries = [listed("2", value=400), listed(1, value=500)]

    decision = qualify(probe(intake_pass(1, entries), intake_pass(2, list(reversed(entries)))))

    assert decision.settled
    assert decision.reason == "coherent"
    assert decision.snapshot is not None
    assert [item.challenge_id.document() for item in decision.snapshot.challenges] == [
        {"type": "integer", "value": "1"},
        {"type": "string", "value": "2"},
    ]
    assert all(
        fact.observed_at == NOW
        for challenge in decision.snapshot.challenges
        for fact in (challenge.value, challenge.solves, challenge.solved)
    )


def test_profile_selected_single_page_shape_uses_the_unmodified_collection_endpoint():
    entries = [listed(1)]

    def single(pass_no):
        observed = intake_pass(pass_no, entries)
        page = replace(
            observed.pages[0],
            endpoint="/api/v1/challenges",
            raw=json.dumps({"success": True, "data": entries}, separators=(",", ":")).encode(),
        )
        page = replace(page, original_bytes=len(page.raw))
        return replace(observed, pages=(page,))

    decision = qualify(probe(single(1), single(2), pagination_shape="single"))

    assert decision.settled


def test_profile_selected_pagination_shape_refuses_the_other_endpoint_shape():
    observed = intake_pass(1, [listed(1)])
    wrong = replace(observed.pages[0], endpoint="/api/v1/challenges")

    decision = qualify(probe(replace(observed, pages=(wrong,)), intake_pass(2, [listed(1)])))

    assert not decision.settled
    assert decision.reason == "endpoint-binding-invalid"


def test_scoreboard_and_mana_are_coherent_timestamped_snapshot_facts():
    entries = [listed(1)]

    def live(pass_no):
        observed = intake_pass(pass_no, entries)
        scoreboard = document(
            "scoreboard",
            {"success": True, "data": {"1": {"name": "team", "score": 900}}},
            pass_no=pass_no,
            request="scoreboard",
            endpoint="/api/v1/scoreboard/top/10",
        )
        mana = document(
            "mana",
            {"success": True, "data": {"used": 1, "total": 5}},
            pass_no=pass_no,
            request="mana",
            endpoint="/api/v1/plugins/ctfd-chall-manager/mana",
        )
        return replace(observed, scoreboard=scoreboard, mana=mana)

    observed = probe(live(1), live(2))
    contract = replace(observed.contract, scoreboard_top=10, mana_outcome="answered")
    decision = qualify(replace(observed, contract=contract))

    assert decision.settled
    assert decision.snapshot is not None
    assert decision.snapshot.scoreboard == (
        {"rank": 1, "name": "team", "score": 900, "observed_at": NOW.isoformat(), "source": "scoreboard"},
    )
    assert decision.snapshot.mana == {
        "outcome": "answered",
        "used": 1,
        "total": 5,
        "observed_at": NOW.isoformat(),
        "source": "mana",
    }
    assert decision.snapshot.digest == hashlib.sha256(decision.snapshot.canonical_bytes()).hexdigest()
    assert decision.snapshot.challenges[0].statement == "statement-1"
    assert decision.snapshot.challenges[0].value.value == 500
    assert decision.snapshot.challenges[0].revision_digest


def test_page_count_shorter_than_published_total_is_unsettled():
    entries = [listed(1)]

    decision = qualify(probe(intake_pass(1, entries, total=2), intake_pass(2, entries, total=2)))

    assert not decision.settled
    assert decision.snapshot is None
    assert decision.reason == "pagination-total-mismatch"


def test_every_published_page_is_read_once_and_page_order_is_not_snapshot_order():
    entries = [listed("z"), listed(2)]

    def paged(pass_no):
        pages = tuple(
            document(
                "list",
                {
                    "success": True,
                    "data": [entry],
                    "meta": {
                        "pagination": {
                            "page": number,
                            "pages": 2,
                            "total": 2,
                            "next": number + 1 if number == 1 else None,
                            "prev": number - 1 if number == 2 else None,
                        }
                    },
                },
                pass_no=pass_no,
                request=f"list-{number}",
                endpoint=f"/api/v1/challenges?page={number}",
                page=number,
            )
            for number, entry in enumerate(entries, 1)
        )
        details = tuple(
            document(
                "detail",
                {"success": True, "data": detail(entry["id"])},
                pass_no=pass_no,
                request=f"detail-{entry['id']}",
                endpoint=f"/api/v1/challenges/{entry['id']}",
                challenge_id=entry["id"],
            )
            for entry in reversed(entries)
        )
        return IntakePass(pass_no, identity(pass_no, "before"), pages, details, (), identity(pass_no, "after"))

    decision = qualify(probe(paged(1), paged(2)))

    assert decision.settled
    assert [one.challenge_id.value for one in decision.snapshot.challenges] == [2, "z"]


def test_changed_statement_between_passes_is_unsettled():
    entries = [listed(1)]
    first = intake_pass(1, entries)
    second = intake_pass(2, entries, detail_overrides={1: detail(1, description="moved")})

    decision = qualify(probe(first, second))

    assert not decision.settled
    assert decision.reason == "passes-disagree"


def test_fresh_identity_controls_bracket_each_pass_and_cannot_change_subject():
    entries = [listed(1)]
    first = intake_pass(1, entries)
    after = document(
        "identity",
        {"success": True, "data": {"id": 99, "name": "someone-else"}},
        pass_no=2,
        request="identity-after",
        endpoint="/api/v1/users/me",
    )
    second = intake_pass(2, entries)
    second = IntakePass(second.number, second.identity_before, second.pages, second.details, (), after)

    decision = qualify(probe(first, second))

    assert not decision.settled
    assert decision.reason == "authentication-changed"


def test_authenticated_two_pass_empty_is_a_settled_first_snapshot():
    decision = qualify(probe(intake_pass(1, []), intake_pass(2, [])))

    assert decision.settled
    assert decision.snapshot is not None
    assert decision.snapshot.challenges == ()
    assert decision.snapshot.empty_diagnosis == "authenticated-empty"


def test_unauthenticated_empty_is_unsettled_not_an_empty_board():
    first = intake_pass(1, [])
    unauthenticated = replace(first.identity_before, status=401, outcome="auth-failure")
    first = replace(first, identity_before=unauthenticated)

    decision = qualify(probe(first, intake_pass(2, [])))

    assert not decision.settled
    assert decision.reason == "document-unsettled"


def test_landing_page_catch_all_empty_is_unsettled_not_an_empty_board():
    first = intake_pass(1, [])
    catch_all = replace(
        first.pages[0],
        raw=b"<html>landing</html>",
        original_bytes=len(b"<html>landing</html>"),
        content_type="text/html",
    )
    first = replace(first, pages=(catch_all,))

    decision = qualify(probe(first, intake_pass(2, [])))

    assert not decision.settled
    assert decision.reason == "document-unsettled"


def test_partial_empty_page_is_unsettled_not_an_empty_board():
    decision = qualify(probe(intake_pass(1, [], total=1), intake_pass(2, [], total=1)))

    assert not decision.settled
    assert decision.reason == "pagination-total-mismatch"


def test_prior_challenge_cannot_disappear_without_two_genuine_not_found_controls():
    entries = [listed(1)]
    prior_decision = qualify(probe(intake_pass(1, entries), intake_pass(2, entries)))
    assert prior_decision.snapshot is not None

    decision = qualify(probe(intake_pass(1, []), intake_pass(2, []), prior=prior_decision.snapshot))

    assert not decision.settled
    assert decision.reason == "tombstone-unproved"


def test_prior_challenge_disappears_only_after_each_pass_proves_structured_absence():
    entries = [listed(1)]
    prior_decision = qualify(probe(intake_pass(1, entries), intake_pass(2, entries)))
    assert prior_decision.snapshot is not None

    def emptied(pass_no):
        observed = intake_pass(pass_no, [])
        absent = document(
            "absence",
            {"success": False, "errors": {"id": "not found"}},
            pass_no=pass_no,
            request="absence-1",
            endpoint="/api/v1/challenges/1",
            challenge_id=1,
        )
        absent = IntakeDocument(**{**absent.__dict__, "status": 404})
        return IntakePass(
            observed.number,
            observed.identity_before,
            observed.pages,
            observed.details,
            (),
            observed.identity_after,
            (absent,),
        )

    decision = qualify(probe(emptied(1), emptied(2), prior=prior_decision.snapshot))

    assert decision.settled
    assert decision.snapshot is not None
    assert decision.snapshot.challenges == ()
    assert decision.snapshot.empty_diagnosis == "authenticated-tombstones"
    tombstone = decision.snapshot.tombstones[0]
    assert tombstone.challenge_id.value == 1
    assert tombstone.prior_revision_digest == prior_decision.snapshot.challenges[0].revision_digest
    assert tombstone.prior_snapshot_digest == prior_decision.snapshot.digest
    assert [item["status"] for item in tombstone.absence_evidence] == [404, 404]

    carried = qualify(probe(intake_pass(1, []), intake_pass(2, []), prior=decision.snapshot))

    assert carried.settled
    assert carried.snapshot is not None
    assert carried.snapshot.tombstones == decision.snapshot.tombstones


def test_re_signed_attachment_urls_compare_by_qualified_resource_identity_and_bytes():
    entries = [listed(1)]

    def with_file(pass_no, listing):
        observed = intake_pass(pass_no, entries, detail_overrides={1: detail(1) | {"files": [listing]}})
        return IntakePass(
            observed.number,
            observed.identity_before,
            observed.pages,
            observed.details,
            (attachment(pass_no, 1, listing, b"archive-bytes"),),
            observed.identity_after,
        )

    decision = qualify(probe(with_file(1, COVER), with_file(2, COVER_RESIGNED)))

    assert decision.settled
    held = decision.snapshot.challenges[0].attachments[0]
    assert held.resource_identity == "files/1a1a1a/cover.png"
    assert held.content_digest == hashlib.sha256(b"archive-bytes").hexdigest()
    assert held.nbytes == len(b"archive-bytes")
    assert held.outcome == "held"


def test_same_attachment_identity_with_different_complete_bytes_is_unsettled():
    entries = [listed(1)]

    def with_file(pass_no, body):
        observed = intake_pass(pass_no, entries, detail_overrides={1: detail(1) | {"files": [COVER]}})
        return IntakePass(
            observed.number,
            observed.identity_before,
            observed.pages,
            observed.details,
            (attachment(pass_no, 1, COVER, body),),
            observed.identity_after,
        )

    decision = qualify(probe(with_file(1, b"first"), with_file(2, b"second")))

    assert not decision.settled
    assert decision.reason == "passes-disagree"


def test_revision_tracks_statement_and_attachment_content_but_not_moving_score_facts():
    entries = [listed(1)]
    original = qualify(probe(intake_pass(1, entries), intake_pass(2, entries))).snapshot.challenges[0]
    rescored_entries = [listed(1, value=450, solves=9)]
    rescored = qualify(probe(intake_pass(1, rescored_entries), intake_pass(2, rescored_entries))).snapshot.challenges[0]
    restated = qualify(
        probe(
            intake_pass(1, entries, detail_overrides={1: detail(1, description="new statement")}),
            intake_pass(2, entries, detail_overrides={1: detail(1, description="new statement")}),
        )
    ).snapshot.challenges[0]

    def with_file(pass_no, body):
        observed = intake_pass(pass_no, entries, detail_overrides={1: detail(1) | {"files": [COVER]}})
        return replace(
            observed,
            attachments=(attachment(pass_no, 1, COVER, body),),
        )

    first_file = qualify(probe(with_file(1, b"first"), with_file(2, b"first"))).snapshot.challenges[0]
    replaced_file = qualify(probe(with_file(1, b"second"), with_file(2, b"second"))).snapshot.challenges[0]

    assert rescored.revision_digest == original.revision_digest
    assert (rescored.value.value, rescored.solves.value) == (450, 9)
    assert restated.revision_digest != original.revision_digest
    assert replaced_file.revision_digest != first_file.revision_digest


def test_repeated_transport_failure_never_settles_an_attachment():
    entries = [listed(1)]

    def with_failure(pass_no):
        observed = intake_pass(pass_no, entries, detail_overrides={1: detail(1) | {"files": [COVER]}})
        return IntakePass(
            observed.number,
            observed.identity_before,
            observed.pages,
            observed.details,
            (attachment(pass_no, 1, COVER, b"", outcome="unreachable", complete=False),),
            observed.identity_after,
        )

    decision = qualify(probe(with_failure(1), with_failure(2)))

    assert not decision.settled
    assert decision.reason == "attachment-unsettled"


def test_every_redirect_hop_is_preserved_while_volatile_signatures_do_not_fake_change():
    entries = [listed(1)]

    def redirected(pass_no, signature):
        listing = f"files/1a1a1a/cover.png?token={signature}"
        target = f"https://cdn.example/archive.bin?signature={signature}"
        observed = intake_pass(pass_no, entries, detail_overrides={1: detail(1) | {"files": [listing]}})
        first = replace(
            attachment(pass_no, 1, listing, b"redirect-body" + signature.encode()),
            status=302,
            content_type="text/plain",
            location=target,
            resource_identity=listing,
            auth_forwarded=True,
            hop=0,
        )
        final = replace(
            attachment(pass_no, 1, target, b"archive-bytes"),
            resource_identity=listing,
            auth_forwarded=False,
            hop=1,
        )
        return IntakePass(
            observed.number,
            observed.identity_before,
            observed.pages,
            observed.details,
            (first, final),
            observed.identity_after,
        )

    decision = qualify(probe(redirected(1, "first"), redirected(2, "second")))

    assert decision.settled
    hops = decision.snapshot.challenges[0].attachments[0].hops
    assert len(hops) == 2
    assert hops[0]["status"] == 302
    assert hops[0]["raw_digest"] == hashlib.sha256(b"redirect-bodysecond").hexdigest()
    assert hops[1]["auth_forwarded"] is False


def test_two_proved_policy_over_limit_reads_settle_without_inventing_attachment_content():
    entries = [listed(1)]

    def over_limit(pass_no):
        observed = intake_pass(pass_no, entries, detail_overrides={1: detail(1) | {"files": [COVER]}})
        refused = replace(
            attachment(pass_no, 1, COVER, b"12345"),
            outcome="too-large",
            complete=False,
            resource_identity=COVER,
        )
        return IntakePass(
            observed.number,
            observed.identity_before,
            observed.pages,
            observed.details,
            (refused,),
            observed.identity_after,
        )

    decision = qualify(probe(over_limit(1), over_limit(2), max_fetch_bytes=4))

    assert decision.settled
    refused = decision.snapshot.challenges[0].attachments[0]
    assert refused.outcome == "over-limit"
    assert refused.content_digest == ""
    assert refused.nbytes == 0
    assert refused.complete is False
    assert refused.reason == "policy-fetch-limit"
    assert refused.original_bytes == 5
    assert refused.stored_bytes == 5
    assert refused.over_limit == {"cap": 4, "lower_bound": 5}


def test_first_boot_unsettled_is_not_represented_as_an_empty_snapshot():
    entries = [listed(1)]
    decision = qualify(
        probe(
            intake_pass(1, entries),
            intake_pass(2, entries, detail_overrides={1: detail(1, description="moved")}),
        )
    )

    result = availability(decision, prior=None, retry_at=NOW + dt.timedelta(seconds=5))

    assert isinstance(result, NoCoherentSnapshot)
    assert result.decision is decision
    assert result.retry_at > NOW


def test_failed_refresh_returns_the_exact_prior_snapshot_with_attempt_separate():
    entries = [listed(1)]
    accepted = qualify(probe(intake_pass(1, entries), intake_pass(2, entries)))
    assert accepted.snapshot is not None
    changed = qualify(
        probe(
            intake_pass(1, entries),
            intake_pass(2, entries, detail_overrides={1: detail(1, description="moved")}),
        )
    )

    result = availability(changed, prior=accepted.snapshot, retry_at=NOW + dt.timedelta(seconds=5))

    assert isinstance(result, SnapshotAvailable)
    assert result.snapshot is accepted.snapshot
    assert result.refresh is changed

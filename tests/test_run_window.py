"""The Run's window is an absolute moment on disk, and a restart may not extend it.

The failure this guards against is silent and unattended. A window counted from process start is
reset by every restart, so a Solver that crashed at 14:00 would be handed a fresh 5.5 hours and
would still be working at 19:30 against a competition that closed at 16:00, with nobody there to
see it. Nothing else about a Run makes that mistake visible — the stream would look ordinary the
whole way.

This is also the one thing the Solver reads back out of `/state`, which is otherwise output and
never input (`CONTEXT.md`, *Run state*). Losing the ledger to a restart costs accuracy; losing the
moment we stop costs the window itself.
"""

import datetime as dt
import json

import pytest
from solver.schedule import WINDOW, Window

OPENED = dt.datetime(2026, 9, 22, 10, 30, tzinfo=dt.timezone.utc)
COMPETITION = 5.5 * 3600


@pytest.fixture
def run_dir(tmp_path):
    return tmp_path / "runs" / "run-1"


def test_the_window_is_written_to_disk_as_two_absolute_moments(run_dir):
    """Absolute, and both ends of it: the close is what stops the Run, and the open is what the
    `f × T_total` spend ceiling is a share of."""
    window = Window.opened(run_dir, lasting=COMPETITION, now=OPENED)

    assert window.ends_at == OPENED + dt.timedelta(hours=5.5)
    assert window.total_seconds == COMPETITION
    stamped = json.loads((run_dir / WINDOW).read_text())
    assert dt.datetime.fromisoformat(stamped["ends_at"]) == window.ends_at
    assert dt.datetime.fromisoformat(stamped["opened_at"]) == OPENED


def test_a_restarted_solver_does_not_extend_its_own_window(run_dir):
    """The whole point. A restart two and a half hours in is handed the three hours that are left,
    not the five and a half it would have got from a clock counted off its own start."""
    Window.opened(run_dir, lasting=COMPETITION, now=OPENED)
    restarted_at = OPENED + dt.timedelta(hours=2.5)

    came_back = Window.opened(run_dir, lasting=COMPETITION, now=restarted_at)

    assert came_back.ends_at == OPENED + dt.timedelta(hours=5.5)
    assert came_back.left(restarted_at) == 3 * 3600
    assert came_back.restarted


def test_the_duration_is_consulted_only_when_there_is_no_window_on_disk(run_dir):
    """A restart under a different configuration is still the same Run. The stamp wins, so a
    duration typed differently on the second boot cannot lengthen a window already running."""
    Window.opened(run_dir, lasting=COMPETITION, now=OPENED)

    came_back = Window.opened(run_dir, lasting=99 * 3600, now=OPENED + dt.timedelta(hours=1))

    assert came_back.ends_at == OPENED + dt.timedelta(hours=5.5)


def test_a_window_that_cannot_be_read_is_refused_rather_than_replaced(run_dir):
    """Writing a fresh window over an unreadable one is exactly the silent extension the stamp
    exists to prevent, so the unreadable case is loud."""
    Window.opened(run_dir, lasting=COMPETITION, now=OPENED)
    (run_dir / WINDOW).write_text("{ this is not the window\n")

    with pytest.raises(ValueError, match="cannot be read"):
        Window.opened(run_dir, lasting=COMPETITION, now=OPENED)

"""What we hold, answered by a command rather than by knowing where to look.

[#59](https://github.com/jerome-queck/incypher-ctf/issues/59) left an acceptance criterion unticked
on the belief that no IN-CYPHER account existed. One did — `.env.incypher` was populated the whole
time. The repository could not have said so: `.env.*` is gitignored, correctly, so the *state* can
never be tracked. What it lacked was anything that would *report* it, which is what turned silence
into a wrong answer.

The one distinction these tests exist for is **empty versus absent**. `docs/credentials.md` already
names it as a trap — an empty value occupies its slot in a client's credential search and
authenticates with nothing — and a reporter that collapsed the two would recreate the failure it
was written to prevent.
"""

import credentials_held

SECRET = "CTFD_API_TOKEN"


def env_file(directory, name: str, body: str):
    path = directory / name
    path.write_text(body)
    return path


def test_a_populated_value_is_set_and_its_value_is_never_returned(tmp_path):
    path = env_file(tmp_path, ".env", f"{SECRET}=ctfd_deadbeefcafe\n")

    holdings = credentials_held.read_holdings(path)

    assert holdings[SECRET] is credentials_held.SET
    assert "ctfd_deadbeefcafe" not in repr(holdings)


def test_an_empty_value_is_not_an_absent_one(tmp_path):
    """The trap. Both read as "no usable credential" and they want opposite fixes: fill this in
    versus delete the line, because the empty one shadows a credential that would otherwise work."""
    empty = credentials_held.read_holdings(env_file(tmp_path, ".env", f"{SECRET}=\n"))
    absent = credentials_held.read_holdings(env_file(tmp_path, ".env.other", "CTFD_URL=https://b.example\n"))

    assert empty[SECRET] is credentials_held.EMPTY
    assert absent[SECRET] is credentials_held.ABSENT
    assert credentials_held.EMPTY is not credentials_held.ABSENT


def test_whitespace_only_is_empty(tmp_path):
    """`FOO=   ` looks filled in an editor and is not."""
    assert (
        credentials_held.read_holdings(env_file(tmp_path, ".env", f"{SECRET}=   \n"))[SECRET] is credentials_held.EMPTY
    )


def test_a_commented_line_holds_nothing(tmp_path):
    """The template's own idiom for "not set here" — it must not read as a holding."""
    assert (
        credentials_held.read_holdings(env_file(tmp_path, ".env", f"# {SECRET}=x\n"))[SECRET] is credentials_held.ABSENT
    )


def test_the_report_names_every_declared_secret_even_when_a_file_holds_none(tmp_path):
    """A reporter that listed only what it found could not distinguish a file missing a credential
    from a reader who forgot to ask about it."""
    holdings = credentials_held.read_holdings(env_file(tmp_path, ".env", "CTFD_URL=https://b.example\n"))

    assert set(holdings) >= set(credentials_held.declared_secrets.SECRETS)


def test_the_example_template_is_never_reported_as_a_holding(tmp_path):
    """It is committed and its values are blank by construction; reporting it invites reading a
    template as an inventory."""
    env_file(tmp_path, ".env", f"{SECRET}=real\n")
    env_file(tmp_path, ".env.example", f"{SECRET}=\n")
    env_file(tmp_path, ".env.incypher", f"{SECRET}=real\n")

    assert [path.name for path in credentials_held.env_files(tmp_path)] == [".env", ".env.incypher"]


def test_rendering_prints_no_value(tmp_path, capsys):
    """The one thing this command must never do, asserted on the rendered output rather than on the
    data — printing is where a value would escape."""
    env_file(tmp_path, ".env", f"{SECRET}=ctfd_deadbeefcafe\nTEAM_KEY=super-secret-team-key\n")

    credentials_held.report(tmp_path)

    printed = capsys.readouterr().out
    assert "ctfd_deadbeefcafe" not in printed
    assert "super-secret-team-key" not in printed
    assert SECRET in printed


def test_an_empty_value_makes_the_command_fail(tmp_path):
    """Absent is ordinary — an overlay carries only its own board's values. Empty is a defect, and
    a pre-flight check that reported it without failing would be read as a pass."""
    env_file(tmp_path, ".env", f"{SECRET}=filled\n")
    assert credentials_held.report(tmp_path) == 0

    env_file(tmp_path, ".env", f"{SECRET}=\n")
    assert credentials_held.report(tmp_path) == 1

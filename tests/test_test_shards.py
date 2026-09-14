from scripts.check_test_shards import SHARDS, partition_errors


def test_partition_rejects_every_way_a_test_can_escape_or_repeat() -> None:
    exact = {name: (f"tests/test_{index}.py",) for index, name in enumerate(SHARDS)}
    tracked = tuple(file for files in exact.values() for file in files)

    assert partition_errors(exact, tracked) == ()

    missing = {name: files for name, files in exact.items()}
    missing[SHARDS[0]] = ()
    assert any("empty shards" in error for error in partition_errors(missing, tracked))
    assert any("not assigned" in error for error in partition_errors(missing, tracked))

    duplicate = {name: files for name, files in exact.items()}
    duplicate[SHARDS[1]] += exact[SHARDS[0]]
    assert any("more than once" in error for error in partition_errors(duplicate, tracked))

    unexpected = {name: files for name, files in exact.items()}
    unexpected[SHARDS[0]] += ("tests/test_unknown.py",)
    assert any("not tracked" in error for error in partition_errors(unexpected, tracked))

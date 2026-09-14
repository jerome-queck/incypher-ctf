from scripts import qualification_retention as retention


def test_new_qualification_directory_is_persistent_and_scoped(tmp_path, monkeypatch) -> None:
    root = tmp_path / "qualification"
    monkeypatch.setattr(retention, "RETENTION_ROOT", root)

    directory = retention.new_retained_directory("targets", "target-")

    assert directory.parent == root / "targets"
    assert directory.name.startswith("target-")
    assert directory.is_dir()

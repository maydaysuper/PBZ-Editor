import json
from pathlib import Path
import pytest
from tools import runtime_save_guard as guard


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    root = tmp_path / "live"
    (root / "daily").mkdir(parents=True)
    (root / "main.sav").write_bytes(b"original main")
    (root / "daily/old.sav").write_bytes(b"original old")
    output = tmp_path / "snapshot"
    monkeypatch.setattr(guard, "_game_processes", lambda _: [])
    monkeypatch.setattr(guard, "sources", lambda _: {"progress": (root, True)})
    guard.snapshot(tmp_path / "game", output)
    return root, output


def test_snapshot_is_identical_and_records_all_files(guarded):
    _, output = guarded
    assert guard.verify(output)["identical"]
    manifest = json.loads((output / "snapshot.json").read_text(encoding='utf8'))
    assert len(manifest["groups"]["progress"]["files"]) == 2


def test_restore_repairs_missing_changed_and_new_files(guarded):
    root, output = guarded
    (root / "main.sav").write_bytes(b"test main")
    (root / "daily/old.sav").unlink()
    (root / "test-auto.sav").write_bytes(b"test auto")
    assert not guard.verify(output)["identical"]
    guard.restore(output)
    assert guard.verify(output)["identical"]
    assert (root / "main.sav").read_bytes() == b"original main"
    assert not (root / "test-auto.sav").exists()
    assert list(output.glob("displaced-*/progress/main.sav"))


def test_restore_refuses_running_game(guarded, monkeypatch):
    root, output = guarded
    (root / "main.sav").write_bytes(b"test main")
    monkeypatch.setattr(guard, "_game_processes", lambda _: [123])
    with pytest.raises(RuntimeError, match="退出游戏"):
        guard.restore(output)
    assert (root / "main.sav").read_bytes() == b"test main"


def test_restore_checks_every_backup_before_writing(guarded):
    root, output = guarded
    (root / "main.sav").write_bytes(b"test main")
    (output / "original/progress/daily/old.sav").write_bytes(b"corrupt backup")
    with pytest.raises(RuntimeError, match="快照校验失败"):
        guard.restore(output)
    assert (root / "main.sav").read_bytes() == b"test main"

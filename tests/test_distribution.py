"""Distribution boundaries: no game data or proprietary codec required for units."""
import hashlib
import json
import os
from pathlib import Path
import struct
import threading
import pytest
from pbz_editor import paths, bootstrap
from pbz_editor.compression import OpenOodleCodec, MAX_BYTES
from pbz_editor.core import EditorError
from pbz_editor.pak_reader import PakReader


@pytest.fixture
def codec():
    root = Path(__file__).resolve().parents[1]/'.local/native'
    if not (root/'manifest.json').exists():
        pytest.skip('Build the optional native codec first')
    return OpenOodleCodec(root)


@pytest.mark.parametrize('size', [0, 1, 262143, 262144, 262145, 800000])
def test_open_codec_stored_blocks_and_bounds(codec, size):
    raw = bytes(range(256))*(size//256) + bytes(range(size%256))
    encoded = codec.compress(raw)
    assert codec.decompress(encoded, size) == raw
    assert len(encoded) == size + 2*((size+262143)//262144)
    assert codec.backend == 'bundled-open'


@pytest.mark.parametrize('raw,size', [(b'bad', 20), (b'\xcc\x06x', 2), (b'x', 0), (b'', -1), (b'', MAX_BYTES+1)])
def test_open_codec_rejects_bad_data(codec, raw, size):
    with pytest.raises(EditorError):
        codec.decompress(raw, size)


def test_codec_manifest_tampering_rejected(tmp_path):
    (tmp_path/'pbz_codec.dll').write_bytes(b'not a library')
    (tmp_path/'manifest.json').write_text(json.dumps({'abi': 1, 'library': 'pbz_codec.dll', 'sha256': '0'*64}))
    with pytest.raises(EditorError, match='损坏'):
        OpenOodleCodec(tmp_path)


def test_default_data_root_and_portable_override(tmp_path, monkeypatch):
    app = tmp_path/'application'
    app.mkdir()
    monkeypatch.setattr(paths, 'project_root', lambda: app)
    monkeypatch.setattr(paths.sys, 'frozen', True, raising=False)
    monkeypatch.delenv('PBZ_EDITOR_DATA_DIR', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'user'))
    assert paths.local_dir() == tmp_path/'user/PBZ-Editor'
    (app/'portable.flag').touch()
    assert paths.local_dir() == app/'.local'
    monkeypatch.setenv('PBZ_EDITOR_DATA_DIR', str(tmp_path/'explicit'))
    assert paths.local_dir() == tmp_path/'explicit'


def test_legacy_migration_preserves_both_sides_and_conflicts(tmp_path):
    old, new = tmp_path/'old', tmp_path/'new'
    (old/'backups').mkdir(parents=True)
    (new/'backups').mkdir(parents=True)
    (old/'settings.json').write_text('{"last_save":"test-copy"}')
    (old/'backups/a.sav').write_bytes(b'old')
    (old/'backups/b.sav').write_bytes(b'copy')
    (new/'backups/a.sav').write_bytes(b'new')
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    paths.migrate_legacy(old, new)
    assert before == {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    assert (new/'backups/a.sav').read_bytes() == b'new'
    assert (new/'backups/b.sav').read_bytes() == b'copy'
    report = json.loads((new/'legacy-migration.json').read_text(encoding='utf8'))
    assert report['conflicts'] == [str(Path('backups/a.sav'))]
    assert not report['errors']
    paths.migrate_legacy(old, new)
    assert json.loads((new/'legacy-migration.json').read_text(encoding='utf8')) == report


def test_game_resolution_and_multiple_save_discovery(tmp_path):
    game = tmp_path/'中文目录/PBZ'
    exe = game/'platform/steam/PBZ.exe'
    exe.parent.mkdir(parents=True)
    exe.touch()
    for account in ['111', '222']:
        save = game/'ProjectPBZ/Saved/SaveGames/Steam'/account/'PBZSaveGame.sav'
        save.parent.mkdir(parents=True)
        save.touch()
    assert paths.resolve_game(exe) == game
    assert paths.resolve_game(game/'platform') == game
    assert len(paths.find_saves(game)) == 2
    assert paths.resolve_game(tmp_path) is None


def test_bootstrap_cancel_never_creates_ready_manifest(tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        bootstrap.prepare_data(tmp_path/'missing-game', tmp_path/'data-root', cancel=event)
    assert not (tmp_path/'data-root').exists()


def test_cache_requires_profile_signature_and_both_file_hashes(tmp_path, monkeypatch):
    profile = tmp_path/'profile.json'
    profile.write_text('{}')
    monkeypatch.setattr(bootstrap, 'profile_path', lambda: profile)
    monkeypatch.setattr(bootstrap, 'signature', lambda *_: 'build-A')
    data = tmp_path/'data'
    data.mkdir()
    for name in ['catalog.json', 'regions.json']:
        (data/name).write_text('{}')
    manifest = {'schema': bootstrap.SCHEMA, 'source': 'build-A', 'files': {n: bootstrap.sha(b'{}') for n in ['catalog.json', 'regions.json']}}
    (data/'manifest.json').write_text(json.dumps(manifest))
    assert bootstrap.prepared_catalog('game', tmp_path) == data/'catalog.json'
    (data/'regions.json').write_text('{"bad":true}')
    assert bootstrap.prepared_catalog('game', tmp_path) is None
    (data/'regions.json').write_text('{}')
    monkeypatch.setattr(bootstrap, 'signature', lambda *_: 'build-B')
    assert bootstrap.prepared_catalog('game', tmp_path) is None


def test_pak_rejects_bad_footer_and_out_of_range_index(tmp_path):
    path = tmp_path/'bad.pak'
    path.write_bytes(bytes(221))
    with pytest.raises(ValueError, match='footer'):
        PakReader(path, None)
    raw = bytearray(221)
    struct.pack_into('<IIQQ', raw, 17, 0x5a6f12e1, 12, 9999, 10)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='bounds'):
        PakReader(path, None)
    struct.pack_into('<IIQQ', raw, 17, 0x5a6f12e1, 12, 0, 1)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='checksum'):
        PakReader(path, None)

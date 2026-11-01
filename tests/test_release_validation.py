"""A historical runtime PASS must not silently cover changed writer/codec code."""
import hashlib
from pathlib import Path
import pytest
from tools.release_contents import validation_matches


@pytest.mark.parametrize('changed', [None, 'pbz_editor/core.py', 'pbz_editor/compression.py',
                                   'native/src/lib.rs', 'codec.dll'])
def test_runtime_attestation_requires_exact_implementation_and_codec(tmp_path, changed):
    files = ['pbz_editor/core.py', 'pbz_editor/compression.py', 'native/src/lib.rs']
    for name in files + ['codec.dll']:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    validation = {'implementation_sha256': {f: hashlib.sha256((tmp_path/f).read_bytes()).hexdigest() for f in files},
                  'codec_sha256': hashlib.sha256((tmp_path/'codec.dll').read_bytes()).hexdigest()}
    if changed:
        (tmp_path/changed).write_bytes(b'changed build')
    assert validation_matches(tmp_path, tmp_path/'codec.dll', validation) == (changed is None)


def test_release_filter_ignores_parent_names_but_rejects_bundled_private_data(tmp_path):
    from tools.release_contents import unexpected_runtime_files
    release = tmp_path/'.local/中文 源码/dist/app'
    (release/'_internal').mkdir(parents=True)
    (release/'PBZ-Editor.exe').touch()
    (release/'_internal/pbz_codec.dll').touch()
    assert unexpected_runtime_files(release) == []
    (release/'.local').mkdir()
    (release/'.local/settings.json').write_text('{}', encoding='utf8')
    assert unexpected_runtime_files(release) == [str(Path('.local/settings.json'))]


def test_source_archive_filter_uses_source_relative_paths(tmp_path, monkeypatch):
    import zipfile
    from tools import release_contents as contents
    project = tmp_path/'target/.local/中文 源码'
    (project/'pbz_editor/__pycache__').mkdir(parents=True)
    (project/'pbz_editor/app.py').write_text('# public source', encoding='utf8')
    (project/'pbz_editor/__pycache__/private.pyc').touch()
    (project/'data').mkdir()
    (project/'data/validation.json').write_text('{"private_samples": true}', encoding='utf8')
    (project/'docs').mkdir()
    (project/'docs/runtime-test.md').write_text('private runtime records', encoding='utf8')
    (project/'.local').mkdir()
    (project/'.local/personal.sav').touch()
    monkeypatch.setattr(contents, 'ROOT', project)
    archive = tmp_path/'source.zip'
    contents.source_zip(archive)
    with zipfile.ZipFile(archive) as result:
        assert result.namelist() == ['PBZ-Editor/pbz_editor/app.py']

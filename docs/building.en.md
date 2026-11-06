# Build from source

[简体中文](building.zh-CN.md)

Requires Windows x64, Python 3.12 and Rust `1.99.1-x86_64-pc-windows-gnu`. Dependency installation needs internet access; users of the packaged app do not need these tools.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
rustup toolchain install 1.99.1-x86_64-pc-windows-gnu --profile minimal
rustup override set 1.99.1-x86_64-pc-windows-gnu
.\tools\build_codec.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\tools\build_release.ps1 -Zip
```

Output: the Windows ZIP, source ZIP and SHA256SUMS.txt under `dist/v0.3.5/`. The runtime includes Python, Qt and an open-source decoder. No proprietary Oodle DLL is required.

Run source: `.\.venv\Scripts\python.exe -m pbz_editor.app`. Optional flags: `--language en-US` or `--language zh-CN`. Set `PBZ_EDITOR_DATA_DIR` for isolated test data.

Without local resources, optional real-game integration tests are skipped. Public tests dynamically create synthetic saves. Do not add real player saves or extracted assets. `data/profile.json` contains resource paths, fingerprints, property layouts and key-location references. New rules require actual field-semantic and write-result checks, not just a different build number or fingerprint.

Generate metadata: `python tools/build_catalog.py --game-dir <game-root>`. Read-only parsers prepare Chinese/English metadata, maps and icons from the installed game. Generated data stays private on the user's machine.

The manually triggered Build Windows preview GitHub workflow runs tests, builds archives and checks startup without a game. It does not publish Releases automatically. Source archives and repository staging share the public allowlist in `tools/release_contents.py`, excluding private caches and raw runtime evidence.

Code: `pbz_editor/core.py` handles saves, `draft.py` handles preview and undo, `ui.py` implements the interface, `i18n.py` and `locales/en-US.json` handle language, and `native/` contains the decoder wrapper and pinned upstream source.

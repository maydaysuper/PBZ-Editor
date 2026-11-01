"""Snapshot and byte-verify the user's progress before any runtime experiment.

This helper does not start games or install test saves. Restoring requires that
the game is closed. All paths are pinned in the snapshot manifest.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pbz_editor.core import _game_processes


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(game):
    saved = game / "ProjectPBZ/Saved"
    result = {"progress": (saved / "SaveGames", True), "local_settings": (saved, False)}
    config = Path.home() / "AppData/Local/ProjectPBZ/Saved/Config"
    if config.exists():
        result["config"] = (config, True)
    return result


def files(root, recursive, group):
    if not root.exists():
        return []
    values = list(root.rglob("*")) if recursive else list(root.glob("*.sav"))
    return sorted(p for p in values if p.is_file())


def snapshot(game, output):
    if _game_processes(game):
        raise RuntimeError("游戏正在运行，未创建不稳定快照。")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
                "game_dir": str(game.resolve()), "groups": {}, "state": "snapshot_created"}
    for name, (root, recursive) in sources(game).items():
        group = {"root": str(root.resolve()), "recursive": recursive, "files": []}
        for path in files(root, recursive, name):
            relative = path.relative_to(root)
            copy = output / "original" / name / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            before = digest(path)
            shutil.copy2(path, copy)
            if before != digest(copy) or before != digest(path):
                raise RuntimeError(f"快照期间文件变化：{path}")
            stat = path.stat()
            group["files"].append({"path": relative.as_posix(), "sha256": before,
                                    "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        manifest["groups"][name] = group
    (output / "snapshot.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    report = verify(output)
    if not report["identical"]:
        raise RuntimeError("快照后原目录已变化。")
    print(json.dumps({"snapshot": str(output), "files": sum(len(g["files"]) for g in manifest["groups"].values()),
                      "identical": report["identical"]}, ensure_ascii=False))


def verify(output):
    manifest = json.loads((output / "snapshot.json").read_text(encoding="utf8"))
    result = {"identical": True, "groups": {}}
    for name, group in manifest["groups"].items():
        root = Path(group["root"])
        expected = {f["path"]: f for f in group["files"]}
        present = {p.relative_to(root).as_posix(): p for p in files(root, group["recursive"], name)}
        detail = {"missing": sorted(expected.keys() - present.keys()), "new": sorted(present.keys() - expected.keys()), "changed": [], "mtime_changed": []}
        for relative in expected.keys() & present.keys():
            if digest(present[relative]) != expected[relative]["sha256"]:
                detail["changed"].append(relative)
            if present[relative].stat().st_mtime_ns != expected[relative]["mtime_ns"]:
                detail["mtime_changed"].append(relative)
        if any(detail.values()):
            result["identical"] = False
        result["groups"][name] = detail
    return result


def restore(output):
    manifest = json.loads((output / "snapshot.json").read_text(encoding="utf8"))
    game = Path(manifest["game_dir"])
    if _game_processes(game):
        raise RuntimeError("请先正常退出游戏，恢复未执行。")
    # Validate every original before touching any live file.
    for name, group in manifest["groups"].items():
        for record in group["files"]:
            original = output / "original" / name / record["path"]
            if digest(original) != record["sha256"]:
                raise RuntimeError(f"原始快照校验失败：{original}")
    before = verify(output)
    quarantine = output / ("displaced-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    for name, group in manifest["groups"].items():
        root = Path(group["root"]).resolve()
        originals = {f["path"]: f for f in group["files"]}
        for current in files(root, group["recursive"], name):
            resolved = current.resolve()
            if not resolved.is_relative_to(root):
                raise RuntimeError("恢复路径超出原始目录。")
            relative = resolved.relative_to(root).as_posix()
            if relative not in originals or digest(resolved) != originals[relative]["sha256"]:
                target = quarantine / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, target)
                if relative not in originals:
                    resolved.unlink()
        for relative, record in originals.items():
            destination = root / relative
            if not destination.resolve().is_relative_to(root):
                raise RuntimeError("恢复目标路径超出原始目录。")
            if not destination.exists() or digest(destination) != record["sha256"] or destination.stat().st_mtime_ns != record["mtime_ns"]:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output / "original" / name / relative, destination)
    after = verify(output)
    (output / "restoration-report.json").write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False, indent=2), encoding="utf8")
    if not after["identical"]:
        raise RuntimeError("恢复后仍有差异，请保留全部快照并检查报告。")
    print(json.dumps({"restored": True, "identical": True, "report": str(output / "restoration-report.json")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["snapshot", "verify", "restore"])
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "snapshot":
        snapshot(args.game_dir.resolve(), args.output.resolve())
    elif args.action == "restore":
        restore(args.output.resolve())
    else:
        print(json.dumps(verify(args.output.resolve()), ensure_ascii=False, indent=2))

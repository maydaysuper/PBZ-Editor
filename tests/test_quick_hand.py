import json
import zlib
from pathlib import Path

import pytest

from pbz_editor.core import EditorError, PBZSContainer, SaveSession, _now
from pbz_editor.draft import InventoryDraft
from pbz_editor.quick_hand import ACHIEVEMENT_ID, COMPLETED, QUEST_CLASS, SUCCEEDED, apply_quick_hand


def _branch(branch_id, length, branch_type="EBranchType::None", progress=None, dirty="0001.01.01-00.00.00"):
    return {
        "BranchID": branch_id,
        "TasksProgress": [0] * length if progress is None else progress,
        "BranchType": branch_type,
        "DirtyTime": dirty,
    }


def _quest(active=None):
    specs = [("1", 3), ("3", 3), ("4", 4), ("5", 3), ("1-1", 5), ("3-1", 5), ("4-1", 5), ("5-1", 5), ("6", 3), ("6-1", 5)]
    active = active or {}
    branches = []
    completed = []
    for branch_id, length in specs:
        branch_type, progress = active.get(branch_id, ("EBranchType::None", [0] * length))
        branches.append(_branch(branch_id, length, branch_type, progress, "2026.10.31-19.39.59" if branch_type == COMPLETED else "0001.01.01-00.00.00"))
        if branch_type == COMPLETED:
            completed.append(branch_id)
    return {
        "QuestClass": QUEST_CLASS,
        "QuestBranches": branches,
        "QuestCompletion": "EQuestCompletion::QC_Started",
        "CompletedBranches": completed,
        "ReachedStateNames": [],
    }


def _slot(quest, tags, accessories=None):
    accessories = accessories or {}
    payload = {
        "PBZGameSystem": {"CycleCount": 1, "HasGameTime": 10},
        "Inventory": {
            "MainWeaponInfos": {"IronBlade": {"WeaponLevel": 4, "AlreadySnapSoul": 4, "Keep": "KEEP_LITERAL"}},
            "MainWeapons": ["IronBlade"],
            "AccessoryInfos": accessories,
            "AccessoryIDs": ["None"],
        },
        "Narrative": {
            "NarrativeSavedQuests": [
                {"QuestClass": "/Game/PBZstory/PBZQuest/Simple/Other.Other_C", "Marker": "KEEP_LITERAL", "QuestBranches": []},
                quest,
            ],
        },
        "EventPipeline": {"GlobalTags": tags},
        "Unknown": "KEEP_LITERAL",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode().replace(b'"KEEP_LITERAL"', b"1.2300e-9")


def _done_forest():
    return {
        "1": (COMPLETED, [0, 0, 0]),
        "1-1": (COMPLETED, [0, 0, 0, 0, 0]),
        "3": ("EBranchType::BranchActivating", [1, 0, 0]),
    }


TAGS = ["fjzl.小张收集物.1", "fjzl.小张收集物.2", "fjzl.小张收集物.3", "fjzl.藏身处小张.收集完奇珍异宝", "ljc.小张收集物.1", "keep.me"]


def test_village_chapter_completes_only_its_branches_tags_and_reward():
    raw = _slot(_quest(_done_forest()), TAGS)
    updated, achievement, changes = apply_quick_hand(raw, b'{"UnlockedIds":["ACH_001"],"Counters":{"ACH_028":1}}', "village", dirty_time="2026.11.06-12.00.00", grant_reward=True)
    quest = json.loads(updated)["Narrative"]["NarrativeSavedQuests"][1]
    branches = {branch["BranchID"]: branch for branch in quest["QuestBranches"]}
    assert branches["3"]["BranchType"] == COMPLETED
    assert branches["3"]["TasksProgress"] == [0, 0, 0]
    assert branches["3"]["DirtyTime"] == "2026.11.06-12.00.00"
    assert branches["3-1"]["BranchType"] == COMPLETED
    assert branches["3-1"]["TasksProgress"] == [0, 0, 0, 0, 0]
    assert branches["1"]["DirtyTime"] == "2026.10.31-19.39.59"
    assert branches["6"]["BranchType"] == "EBranchType::None"
    assert quest["QuestCompletion"] == "EQuestCompletion::QC_Started"
    assert quest["CompletedBranches"] == ["1", "1-1", "3", "3-1"]
    tags = json.loads(updated)["EventPipeline"]["GlobalTags"]
    assert tags[:6] == TAGS
    assert tags[6:] == ["ljc.小张收集物.2", "ljc.小张收集物.3", "ljc.藏身处小张.收集完奇珍异宝", "achievment.妙手拾遗.完成"]
    assert "xm.小张收集物.1" not in tags
    accessory = json.loads(updated)["Inventory"]["AccessoryInfos"]["AC_Control"]
    assert accessory["CurrentLevel"] == 0
    assert json.loads(updated)["Inventory"]["MainWeaponInfos"]["IronBlade"]["AlreadySnapSoul"] == 4
    assert branches["4"]["BranchType"] == "EBranchType::None"
    assert b"1.2300e-9" in updated
    saved = json.loads(achievement)
    assert saved["UnlockedIds"] == ["ACH_001", ACHIEVEMENT_ID]
    assert saved["Counters"] == {"ACH_028": 1}
    assert "PlatformPending" not in saved
    assert any(change["id"] == ACHIEVEMENT_ID for change in changes)


def test_estate_chapter_writes_achievement_without_rewriting_other_regions():
    active = {branch_id: (COMPLETED, [0] * length) for branch_id, length in [
        ("1", 3), ("1-1", 5), ("3", 3), ("3-1", 5), ("4", 4), ("4-1", 5), ("5", 3), ("5-1", 5),
    ]}
    raw = _slot(_quest(active), TAGS + ["ljc.小张收集物.2", "ljc.小张收集物.3", "ljc.藏身处小张.收集完奇珍异宝"], {"AC_Control": {"CurrentLevel": 2}})
    achievements = b'{"UnlockedIds":["ACH_001"],"Counters":{"ACH_028":1},"CompletedElements":{"ACH_028":["a"]},"PlatformPending":[]}'
    updated, achievement, _changes = apply_quick_hand(raw, achievements, "estate", dirty_time="2026.11.06-12.00.00", grant_reward=True)
    quest = json.loads(updated)["Narrative"]["NarrativeSavedQuests"][1]
    assert quest["QuestCompletion"] == SUCCEEDED
    assert {branch["BranchID"] for branch in quest["QuestBranches"] if branch["BranchType"] == COMPLETED} >= set(("6", "6-1"))
    assert json.loads(updated)["Inventory"]["AccessoryInfos"]["AC_Control"]["CurrentLevel"] == 2
    assert json.loads(updated)["Inventory"]["AccessoryInfos"]["AC_Electrified"]["CurrentLevel"] == 0
    saved = json.loads(achievement)
    assert saved["UnlockedIds"] == ["ACH_001", ACHIEVEMENT_ID]
    assert saved["Counters"] == {"ACH_028": 1}
    assert saved["CompletedElements"] == {"ACH_028": ["a"]}
    assert saved["PlatformPending"] == []
    again, achievement_again, _changes = apply_quick_hand(updated, achievement, "estate", dirty_time="2026.11.06-13.00.00", grant_reward=True)
    assert again == updated
    assert achievement_again == achievement


def test_rerun_on_delivered_chapter_grants_missing_achievement_and_leaves_other_branches():
    active = {branch_id: (COMPLETED, [0] * length) for branch_id, length in [
        ("1", 3), ("1-1", 5), ("3", 3), ("3-1", 5), ("4", 4), ("4-1", 5), ("5", 3), ("5-1", 5),
        ("6", 3), ("6-1", 5),
    ]}
    quest = _quest(active)
    for branch in quest["QuestBranches"]:
        if branch["BranchID"] in ("3", "3-1"):
            del branch["TasksProgress"]
    quest["QuestBranches"].append({
        "BranchID": "2", "BranchType": "EBranchType::None", "DirtyTime": "0001.01.01-00.00.00",
    })
    tags = TAGS + [
        "ljc.小张收集物.2", "ljc.小张收集物.3", "ljc.藏身处小张.收集完奇珍异宝",
        "xm.小张收集物.1", "xm.小张收集物.2", "xm.小张收集物.3", "xm.藏身处小张.收集完奇珍异宝",
    ]
    raw = _slot(quest, tags, {"AC_Control": {"CurrentLevel": 4}})
    achievements = b'{"UnlockedIds":["ACH_001"],"Counters":{"ACH_028":1},"CompletedElements":{"ACH_028":["a"]},"PlatformPending":[]}'
    updated, achievement, _changes = apply_quick_hand(
        raw, achievements, "village", dirty_time="2099.01.01-00.00.00", grant_reward=True)
    saved_quest = json.loads(updated)["Narrative"]["NarrativeSavedQuests"][1]
    branches = {branch["BranchID"]: branch for branch in saved_quest["QuestBranches"]}
    assert "TasksProgress" not in branches["3"]
    assert branches["3"]["DirtyTime"] == "2026.10.31-19.39.59"
    assert branches["2"] == {"BranchID": "2", "BranchType": "EBranchType::None", "DirtyTime": "0001.01.01-00.00.00"}
    assert branches["4"]["BranchType"] == COMPLETED
    assert branches["5"]["BranchType"] == COMPLETED
    assert saved_quest["QuestCompletion"] == "EQuestCompletion::QC_Started"
    saved = json.loads(achievement)
    assert saved["UnlockedIds"] == ["ACH_001", ACHIEVEMENT_ID]
    assert saved["Counters"] == {"ACH_028": 1}
    assert saved["CompletedElements"] == {"ACH_028": ["a"]}
    assert saved["PlatformPending"] == []
    assert json.loads(updated)["Inventory"]["AccessoryInfos"]["AC_Control"]["CurrentLevel"] == 4
    assert json.loads(updated)["Inventory"]["MainWeaponInfos"]["IronBlade"]["AlreadySnapSoul"] == 4
    assert "achievment.妙手拾遗.完成" in json.loads(updated)["EventPipeline"]["GlobalTags"]


def test_estate_alone_writes_achievement_and_does_not_complete_village():
    raw = _slot(_quest(), TAGS)
    updated, achievement, _changes = apply_quick_hand(
        raw, b'{"UnlockedIds":["ACH_014"],"PlatformPending":[]}', "estate",
        dirty_time="2026.11.06-12.00.00", grant_reward=True)
    branches = {branch["BranchID"]: branch for branch in json.loads(updated)["Narrative"]["NarrativeSavedQuests"][1]["QuestBranches"]}
    assert branches["6"]["BranchType"] == COMPLETED
    assert branches["3"]["BranchType"] == "EBranchType::None"
    assert branches["1"]["BranchType"] == "EBranchType::None"
    assert branches["4"]["BranchType"] == "EBranchType::None"
    saved = json.loads(achievement)
    assert saved["UnlockedIds"] == ["ACH_014", ACHIEVEMENT_ID]
    assert saved["PlatformPending"] == []


def test_refuses_a_branch_whose_task_list_does_not_match_and_writes_nothing():
    quest = _quest(_done_forest())
    quest["QuestBranches"][1]["TasksProgress"] = [1, 0]
    raw = _slot(quest, TAGS)
    with pytest.raises(EditorError, match="分支 3"):
        apply_quick_hand(raw, b'{"UnlockedIds":[]}', "village", dirty_time=_now(), grant_reward=True)


def test_refuses_a_save_without_the_quest_record():
    raw = _slot(_quest(), TAGS)
    payload = json.loads(raw)
    payload["Narrative"]["NarrativeSavedQuests"] = payload["Narrative"]["NarrativeSavedQuests"][:1]
    raw = json.dumps(payload, separators=(",", ":")).encode()
    with pytest.raises(EditorError, match="没有可识别的妙手空空"):
        apply_quick_hand(raw, None, "estate", dirty_time=_now(), grant_reward=True)


def _session(tmp_path, slot_raw, achievements=b'{"UnlockedIds":["ACH_001"]}'):
    container = PBZSContainer(bytes(range(32)))
    entries = []
    for name, raw in [
        ("__Advanced__", b'{"LastContinueSlot":"AutoSave0"}'),
        ("AutoSave0", slot_raw),
        ("ManualSave1", slot_raw),
        ("__Achievements__", achievements),
    ]:
        entries.append({"name": name, "algorithm": 1, "flag": 1, "raw": raw, "json": json.loads(raw),
                        "block": container.seal(zlib.compress(raw))})
    source = container.encode(entries, {})
    path = tmp_path / "PBZSaveGame.sav"
    path.write_bytes(source)
    session = SaveSession(path, tmp_path, container, source, tmp_path / "private")
    return session


def test_save_preserves_other_slots_and_keeps_backup(tmp_path, monkeypatch):
    monkeypatch.setattr("pbz_editor.core._game_processes", lambda _path: [])
    raw = _slot(_quest(_done_forest()), TAGS)
    session = _session(tmp_path, raw)
    before = {entry["name"]: entry["block"] for entry in session.entries}
    result = session.apply("AutoSave0", [{"action": "complete_quick_hand", "chapter": "village", "dirty_time": "2026.11.06-12.00.00"}])
    assert result["operation_count"] >= 1
    assert Path(result["backup_path"]).read_bytes() != session.save_path.read_bytes()
    reloaded = session.container.decode(session.save_path.read_bytes())
    blocks = {entry["name"]: entry["block"] for entry in reloaded}
    assert blocks["__Advanced__"] == before["__Advanced__"]
    assert blocks["ManualSave1"] == before["ManualSave1"]
    assert blocks["__Achievements__"] != before["__Achievements__"]
    quest = next(entry["json"] for entry in reloaded if entry["name"] == "AutoSave0")
    assert "AC_Control" in quest["Inventory"]["AccessoryInfos"]
    assert quest["Inventory"]["MainWeaponInfos"]["IronBlade"]["AlreadySnapSoul"] == 4
    unlocked = next(entry["json"] for entry in reloaded if entry["name"] == "__Achievements__")
    assert unlocked["UnlockedIds"] == ["ACH_001", ACHIEVEMENT_ID]


def test_view_only_accessories_skip_the_reward_and_still_complete_the_chapter(tmp_path):
    raw = _slot(_quest(_done_forest()), TAGS)
    session = _session(tmp_path, raw)
    session.compatibility = {
        'executable': 'changed', 'available_categories': ['goods'],
        'unavailable_categories': {}, 'warnings': [], 'structural_categories': [],
    }
    preview = session.preview('AutoSave0', [{'action': 'complete_quick_hand', 'chapter': 'village', 'dirty_time': '2026.11.06-12.00.00'}])
    assert any(change['category'] == 'quest' for change in preview['changes'])
    assert all(change.get('category') != 'accessory' for change in preview['changes'])
    view = session.project('AutoSave0', [{'action': 'complete_quick_hand', 'chapter': 'village', 'dirty_time': '2026.11.06-12.00.00'}])
    slot = next(entry['json'] for entry in view.entries if entry['name'] == 'AutoSave0')
    assert 'AC_Control' not in slot['Inventory']['AccessoryInfos']
    assert 'ljc.藏身处小张.收集完奇珍异宝' in slot['EventPipeline']['GlobalTags']
    assert 'achievment.妙手拾遗.完成' in slot['EventPipeline']['GlobalTags']
    assert next(entry['json'] for entry in view.entries if entry['name'] == '__Achievements__')['UnlockedIds'] == ['ACH_001', ACHIEVEMENT_ID]
    assert slot['Narrative']['NarrativeSavedQuests'][1]['QuestBranches'][2]['BranchType'] == 'EBranchType::None'


def test_read_only_save_refuses_the_chapter(tmp_path):
    raw = _slot(_quest(_done_forest()), TAGS)
    session = _session(tmp_path, raw)
    flagged = bytearray(session.source)
    flagged[4] = 4
    session.source = bytes(flagged)
    with pytest.raises(EditorError, match="仅可查看"):
        session.preview("AutoSave0", [{"action": "complete_quick_hand", "chapter": "village", "dirty_time": "2026.11.06-12.00.00"}])


def test_draft_previews_reward_and_undoes_it(tmp_path):
    raw = _slot(_quest(_done_forest()), TAGS)
    session = _session(tmp_path, raw)
    draft = InventoryDraft(session, "AutoSave0")
    assert draft.stage({"action": "complete_quick_hand", "chapter": "village", "dirty_time": "2026.11.06-12.00.00"})
    assert draft.items[("accessory", "ac_control")]["level"] == 0
    operations = draft.validated_operations()
    assert any(op.get("action") == "complete_quick_hand" and op.get("chapter") == "village" for op in operations)
    draft.undo()
    assert ("accessory", "ac_control") not in draft.items
    assert draft.quest_ops == []
    assert draft.validated_operations() == []
    assert draft.stage({"action": "complete_quick_hand", "chapter": "village", "dirty_time": "2026.11.06-12.00.00"})
    draft.discard()
    assert draft.quest_ops == []
    assert ("accessory", "ac_control") not in draft.items

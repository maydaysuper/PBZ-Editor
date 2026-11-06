"""Mark two observed 妙手空空 chapters complete.

The layout comes from a public Cycle 10 PBZS slot, not from a guessed blob.
MiaoShouKongKong stores one quest. Each region is a collect branch plus a
delivery branch. A finished region in that save uses BranchCompleted, zeroes
TasksProgress, lists the branch ids in CompletedBranches, and sets gameplay
tags ``{prefix}.小张收集物.1/2/3`` and ``{prefix}.藏身处小张.收集完奇珍异宝``.

陆家村 is branch 3 / 3-1 (3 collect tasks; the in-progress branch while
``ljc.小张收集物.1`` is set). 戏梦山庄 is branch 6 / 6-1. Branch 4 has four
tasks, matching 庞镇's four treasures and bell ``bell_pz_xiao_zhang_003``.
The ``xm`` tag prefix follows bell ``bell_xm_xiao_zhang_005`` the same way
``ljc`` follows ``bell_ljc_xiao_zhang_002`` and the observed ``ljc.`` tags.

Either chapter action also records 妙手拾遗. The achievement slot's unlock
list is ``UnlockedIds``; quest achievements such as ACH_032 sit there and not
in the counter maps. Progress slots also store ``achievment.<中文名>.完成``
(the game's own spelling). ``PlatformPending`` was an empty array, so its
item shape is unknown and is not written. Steam may still not show a popup.
"""
from __future__ import annotations

import copy

from .core import EditorError, LosslessJSON, edit_inventory

QUEST_CLASS = "/Game/PBZstory/PBZQuest/Collect/MiaoShouKongKong.MiaoShouKongKong_C"
COMPLETED = "EBranchType::BranchCompleted"
SUCCEEDED = "EQuestCompletion::QC_Succeded"
ACHIEVEMENT_ID = "ACH_039"
# Observed on unlocked story and side achievements inside the progress slot.
ACHIEVEMENT_TAG = "achievment.妙手拾遗.完成"
REQUIRED_BRANCHES = ("1", "1-1", "3", "3-1", "4", "4-1", "5", "5-1", "6", "6-1")

CHAPTERS = {
    "village": {
        "collect": ("3", 3),
        "deliver": ("3-1", 5),
        "prefix": "ljc",
        "reward": "AC_Control",
        "label": "陆家村的妙手空空",
    },
    "estate": {
        "collect": ("6", 3),
        "deliver": ("6-1", 5),
        "prefix": "xm",
        "reward": "AC_Electrified",
        "label": "戏梦山庄的妙手空空",
    },
}


def _tags_for(prefix):
    return [f"{prefix}.小张收集物.{index}" for index in (1, 2, 3)] + [f"{prefix}.藏身处小张.收集完奇珍异宝"]


def _quest_index(narrative):
    quests = narrative.get("NarrativeSavedQuests") if isinstance(narrative, dict) else None
    if not isinstance(quests, list):
        raise EditorError("存档里没有可识别的妙手空空任务记录，未写入。")
    matches = [index for index, quest in enumerate(quests)
               if isinstance(quest, dict) and quest.get("QuestClass") == QUEST_CLASS]
    if len(matches) != 1:
        raise EditorError("存档里没有可识别的妙手空空任务记录，未写入。")
    return matches[0], quests[matches[0]]


def _branch_index(quest, branch_id):
    branches = quest.get("QuestBranches")
    if not isinstance(branches, list):
        raise EditorError(f"妙手空空缺少分支 {branch_id}，未写入。")
    matches = [index for index, branch in enumerate(branches)
               if isinstance(branch, dict) and branch.get("BranchID") == branch_id]
    if len(matches) != 1:
        raise EditorError(f"妙手空空缺少分支 {branch_id}，未写入。")
    return matches[0]


def _progress_ok(branch, length):
    progress = branch.get("TasksProgress")
    return (isinstance(progress, list) and len(progress) == length
            and all(type(item) is int for item in progress))


def _already_delivered(branch, completed_ids):
    """The game can show a chapter done without the exact progress array we write.

    Completed saves sometimes omit TasksProgress, and a dormant extra branch can
    stay unfinished. Neither should block ACH_039 or cause us to rewrite it.
    """
    branch_type = branch.get("BranchType")
    if isinstance(branch_type, str) and branch_type.endswith("BranchCompleted"):
        return True
    return branch.get("BranchID") in completed_ids


def _fully_succeeded(quest):
    branches = quest.get("QuestBranches")
    if not isinstance(branches, list):
        return False
    by_id = {branch.get("BranchID"): branch for branch in branches if isinstance(branch, dict)}
    if any(branch_id not in by_id for branch_id in REQUIRED_BRANCHES):
        return False
    return all(branch.get("BranchType") == COMPLETED for branch in branches if isinstance(branch, dict))


def _reward_missing(editor, reward_id):
    infos = editor.value.get("Inventory", {}).get("AccessoryInfos", {})
    if not isinstance(infos, dict):
        return True
    matches = [key for key in infos if isinstance(key, str) and key.casefold() == reward_id.casefold()]
    if len(matches) > 1:
        raise EditorError(f"存档中存在大小写冲突的物品 ID：{reward_id}。")
    return not matches


def _append_achievement(raw):
    """Append ACH_039 to UnlockedIds. Leave counters and PlatformPending alone."""
    editor = LosslessJSON(raw)
    unlocked = editor.value.get("UnlockedIds")
    if not isinstance(unlocked, list) or any(not isinstance(item, str) for item in unlocked):
        return raw, "unreadable"
    if ACHIEVEMENT_ID in unlocked:
        return raw, "present"
    editor.set(["UnlockedIds"], [*unlocked, ACHIEVEMENT_ID])
    return editor.bytes(), "written"


def apply_quick_hand(slot_raw, achievement_raw, chapter, *, dirty_time, grant_reward):
    """Return updated slot bytes, achievement bytes, and change records.

    ``achievement_raw`` may be None when the save has no achievement slot.
    A missing or unexpected UnlockedIds list does not block the chapter write.
    """
    spec = CHAPTERS.get(chapter)
    if spec is None:
        raise EditorError("未知的妙手空空章节。")
    if not isinstance(dirty_time, str) or not dirty_time or len(dirty_time) > 32 or any(ord(char) < 32 for char in dirty_time):
        raise EditorError("任务时间无效。")
    editor = LosslessJSON(slot_raw)
    quest_index, quest = _quest_index(editor.value.get("Narrative"))
    targets = (spec["collect"], spec["deliver"])
    positions = {branch_id: _branch_index(quest, branch_id) for branch_id, _length in targets}
    pipeline = editor.value.get("EventPipeline")
    tags = pipeline.get("GlobalTags") if isinstance(pipeline, dict) else None
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise EditorError("存档里没有可识别的剧情标记，未写入。")
    completed = quest.get("CompletedBranches", [])
    if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
        raise EditorError("妙手空空的已完成分支列表无法识别，未写入。")

    quest_path = ["Narrative", "NarrativeSavedQuests", quest_index]
    changed_branches = []
    delivered_ids = set(completed)
    for branch_id, length in targets:
        position = positions[branch_id]
        branch = quest["QuestBranches"][position]
        if _already_delivered(branch, delivered_ids):
            continue
        if not _progress_ok(branch, length):
            raise EditorError(f"妙手空空分支 {branch_id} 的任务进度长度不是 {length}，未写入。")
        base = [*quest_path, "QuestBranches", position]
        editor.set([*base, "BranchType"], COMPLETED)
        editor.set([*base, "TasksProgress"], [0] * length)
        editor.set([*base, "DirtyTime"], dirty_time)
        changed_branches.append(branch_id)
    merged = list(completed)
    for branch_id, _length in targets:
        if branch_id not in merged and editor.value["Narrative"]["NarrativeSavedQuests"][quest_index]["QuestBranches"][positions[branch_id]].get("BranchType") == COMPLETED:
            merged.append(branch_id)
    if merged != completed:
        editor.set([*quest_path, "CompletedBranches"], merged)
    updated = editor.value["Narrative"]["NarrativeSavedQuests"][quest_index]
    succeeded = _fully_succeeded(updated)
    if succeeded and updated.get("QuestCompletion") != SUCCEEDED:
        editor.set([*quest_path, "QuestCompletion"], SUCCEEDED)
    needed = [*_tags_for(spec["prefix"]), ACHIEVEMENT_TAG]
    tag_list = list(tags)
    for tag in needed:
        if tag not in tag_list:
            tag_list.append(tag)
    if tag_list != tags:
        editor.set(["EventPipeline", "GlobalTags"], tag_list)

    raw = editor.bytes()
    reward_changes = []
    if grant_reward and _reward_missing(editor, spec["reward"]):
        raw, reward_changes = edit_inventory(raw, [{
            "action": "add", "id": spec["reward"], "category": "accessory", "quantity": 1, "level": 0,
            "name": spec["reward"],
        }])
    notes = []
    if changed_branches:
        notes.append("已把收集和交付分支标成完成。")
    if any(tag not in tags for tag in _tags_for(spec["prefix"])):
        notes.append("已补上该区域的收集完成标记。")
    if ACHIEVEMENT_TAG not in tags:
        notes.append("已补上妙手拾遗的完成标记。")
    if reward_changes:
        notes.append(f"背包里没有 {spec['reward']}，已按饰品记录补上。")
    elif grant_reward:
        notes.append(f"背包里已有 {spec['reward']}，没有改它的等级。")
    else:
        notes.append("饰品目前仅可查看，所以没有写入奖励。")
    achievement_state = "missing-slot"
    if isinstance(achievement_raw, (bytes, bytearray)):
        achievement_raw, achievement_state = _append_achievement(achievement_raw)
    if achievement_state == "written":
        notes.append("已把妙手拾遗 ACH_039 写入成就槽，并补上完成标记。")
    elif achievement_state == "present":
        notes.append("成就槽里已经有 ACH_039。")
    elif achievement_state == "unreadable":
        notes.append("成就槽没有可识别的 UnlockedIds，未写入 ACH_039。")
    else:
        notes.append("存档没有成就槽，未写入 ACH_039。")
    notes.append("Steam 弹窗不保证出现：待弹出列表没有见过具体条目，所以没有改它。")
    changes = [{
        "action": "complete_quick_hand",
        "chapter": chapter,
        "id": chapter,
        "category": "quest",
        "name": spec["label"],
        "before": copy.deepcopy(quest),
        "after": json_quest(raw),
        "note": "".join(notes),
    }]
    changes.extend(reward_changes)
    if achievement_state == "written":
        changes.append({
            "action": "complete_quick_hand",
            "chapter": chapter,
            "id": ACHIEVEMENT_ID,
            "category": "achievement",
            "name": "妙手拾遗",
            "slot": "__Achievements__",
            "scope": "achievements",
            "note": "只把 ACH_039 加进 UnlockedIds，不改计数、已完成元素或待弹出列表。Steam 弹窗不由这份存档单独保证。",
        })
    return raw, achievement_raw, changes


def json_quest(raw):
    editor = LosslessJSON(raw)
    _index, quest = _quest_index(editor.value.get("Narrative"))
    return copy.deepcopy(quest)

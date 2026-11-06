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

The menu actions do not finish the delivery and do not write Steam achievements.
Collection stays finished. The deliver branch is put back to the not-started
shape stored on dormant branches in that save: ``EBranchType::None``, zero
progress, and ``0001.01.01-00.00.00``. The game's own talk with 小张 is what
should complete the delivery and unlock 妙手拾遗.

shortcut: deliver-reopen mix not taken from a live hand-in save, upgrade when one exists.
"""
from __future__ import annotations

import copy

from .core import EditorError, LosslessJSON

QUEST_CLASS = "/Game/PBZstory/PBZQuest/Collect/MiaoShouKongKong.MiaoShouKongKong_C"
COMPLETED = "EBranchType::BranchCompleted"
NONE = "EBranchType::None"
# Unfinished quests in the public Cycle 10 slot, including MiaoShouKongKong, use this.
STARTED = "EQuestCompletion::QC_Started"
SUCCEEDED = "EQuestCompletion::QC_Succeded"
NOT_STARTED_TIME = "0001.01.01-00.00.00"
# Tag this editor added in v0.3.5. Remove it so the game can set it when the hand-in runs.
ACHIEVEMENT_TAG = "achievment.妙手拾遗.完成"

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
    return [f"{prefix}.小张收集物.{index}" for index in (1, 2, 3)] + [
        f"{prefix}.藏身处小张.收集完奇珍异宝",
    ]


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
    branch_type = branch.get("BranchType")
    if isinstance(branch_type, str) and branch_type.endswith("BranchCompleted"):
        return True
    return branch.get("BranchID") in completed_ids


def _deliver_not_started(branch, completed_ids, length):
    """Observed dormant delivery: None, zero progress, unset timestamp, not completed."""
    if branch.get("BranchID") in completed_ids:
        return False
    if branch.get("BranchType") != NONE or branch.get("DirtyTime") != NOT_STARTED_TIME:
        return False
    return branch.get("TasksProgress") == [0] * length


def apply_quick_hand(slot_raw, chapter, *, dirty_time):
    """Prepare one region's hand-in. Do not write the achievement slot or rewards.

    Collection is marked finished with the branch shape and tags seen on a
    completed region. The deliver branch is restored to the not-started object
    seen on branches 3-1, 4-1, 5-1, and 6-1 before delivery in the public save.

    shortcut: deliver-reopen mix not taken from a live hand-in save, upgrade when one exists.
    """
    spec = CHAPTERS.get(chapter)
    if spec is None:
        raise EditorError("未知的妙手空空章节。")
    if not isinstance(dirty_time, str) or not dirty_time or len(dirty_time) > 32 or any(ord(char) < 32 for char in dirty_time):
        raise EditorError("任务时间无效。")
    editor = LosslessJSON(slot_raw)
    quest_index, quest = _quest_index(editor.value.get("Narrative"))
    collect_id, collect_length = spec["collect"]
    deliver_id, deliver_length = spec["deliver"]
    positions = {collect_id: _branch_index(quest, collect_id), deliver_id: _branch_index(quest, deliver_id)}
    pipeline = editor.value.get("EventPipeline")
    tags = pipeline.get("GlobalTags") if isinstance(pipeline, dict) else None
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise EditorError("存档里没有可识别的剧情标记，未写入。")
    completed = quest.get("CompletedBranches", [])
    if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
        raise EditorError("妙手空空的已完成分支列表无法识别，未写入。")
    delivered_ids = set(completed)
    collect = quest["QuestBranches"][positions[collect_id]]
    deliver = quest["QuestBranches"][positions[deliver_id]]
    if not _already_delivered(collect, delivered_ids) and not _progress_ok(collect, collect_length):
        raise EditorError(f"妙手空空分支 {collect_id} 的任务进度长度不是 {collect_length}，未写入。")
    deliver_progress = deliver.get("TasksProgress")
    if deliver_progress is not None and not _progress_ok(deliver, deliver_length):
        raise EditorError(f"妙手空空分支 {deliver_id} 的任务进度长度不是 {deliver_length}，未写入。")

    quest_path = ["Narrative", "NarrativeSavedQuests", quest_index]
    if not _already_delivered(collect, delivered_ids):
        base = [*quest_path, "QuestBranches", positions[collect_id]]
        editor.set([*base, "BranchType"], COMPLETED)
        editor.set([*base, "TasksProgress"], [0] * collect_length)
        editor.set([*base, "DirtyTime"], dirty_time)
    # shortcut: deliver-reopen mix not taken from a live hand-in save, upgrade when one exists.
    if not _deliver_not_started(deliver, delivered_ids, deliver_length):
        base = [*quest_path, "QuestBranches", positions[deliver_id]]
        if deliver.get("BranchType") != NONE:
            editor.set([*base, "BranchType"], NONE)
        if deliver.get("TasksProgress") != [0] * deliver_length:
            editor.set([*base, "TasksProgress"], [0] * deliver_length)
        if deliver.get("DirtyTime") != NOT_STARTED_TIME:
            editor.set([*base, "DirtyTime"], NOT_STARTED_TIME)
    merged = [item for item in completed if item != deliver_id]
    if collect_id not in merged and _already_delivered(
            editor.value["Narrative"]["NarrativeSavedQuests"][quest_index]["QuestBranches"][positions[collect_id]], set()):
        merged.append(collect_id)
    if merged != completed:
        editor.set([*quest_path, "CompletedBranches"], merged)
    completion = editor.value["Narrative"]["NarrativeSavedQuests"][quest_index].get("QuestCompletion")
    if completion == SUCCEEDED:
        editor.set([*quest_path, "QuestCompletion"], STARTED)
    needed = _tags_for(spec["prefix"])
    tag_list = [tag for tag in tags if tag != ACHIEVEMENT_TAG]
    for tag in needed:
        if tag not in tag_list:
            tag_list.append(tag)
    if tag_list != tags:
        editor.set(["EventPipeline", "GlobalTags"], tag_list)

    raw = editor.bytes()
    if raw == slot_raw:
        return slot_raw, []
    notes = ["收集保持完成。交付改回未开始，进游戏后和该区域的妙手小张对话，由游戏完成交付和成就。"]
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
    return raw, changes


def json_quest(raw):
    editor = LosslessJSON(raw)
    _index, quest = _quest_index(editor.value.get("Narrative"))
    return copy.deepcopy(quest)

"""claude-mem v15 TimelineService.filterByDepth (reference/claude_mem/TimelineService.ts)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


TimelineKind = Literal["observation", "session", "prompt"]


@dataclass
class TimelineItem:
    type: TimelineKind
    data: dict[str, Any]
    epoch: int


def build_timeline(observations: list[dict[str, Any]], sessions=None, prompts=None) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    for obs in observations:
        items.append(TimelineItem("observation", obs, int(obs.get("created_at_epoch", obs.get("epoch", 0)))))
    for sess in sessions or []:
        items.append(TimelineItem("session", sess, int(sess.get("created_at_epoch", sess.get("epoch", 0)))))
    for prompt in prompts or []:
        items.append(TimelineItem("prompt", prompt, int(prompt.get("created_at_epoch", prompt.get("epoch", 0)))))
    items.sort(key=lambda item: item.epoch)
    return items


def filter_by_depth(
    items: list[TimelineItem],
    anchor_id: int | str,
    anchor_epoch: int,
    depth_before: int,
    depth_after: int,
) -> list[TimelineItem]:
    if not items:
        return items
    if isinstance(anchor_id, int):
        anchor_index = next(
            (index for index, item in enumerate(items)
             if item.type == "observation" and item.data.get("id") == anchor_id),
            -1,
        )
        if anchor_index == -1:
            return items
        start = max(0, anchor_index - depth_before)
        end = min(len(items), anchor_index + depth_after + 1)
        return items[start:end]
    before = [item for item in items if item.epoch < anchor_epoch]
    anchor_group = [item for item in items if item.epoch == anchor_epoch]
    after = [item for item in items if item.epoch > anchor_epoch]
    return [
        *before[max(0, len(before) - depth_before):],
        *anchor_group,
        *after[:depth_after],
    ]

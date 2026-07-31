from __future__ import annotations


TRUNCATION_MARKER = "\n…[已按上下文预算截断]"


def clip_text(value: str | None, max_chars: int) -> str:
    text = str(value or "")
    limit = max(0, int(max_chars))
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARKER):
        return TRUNCATION_MARKER[:limit]
    return text[: limit - len(TRUNCATION_MARKER)].rstrip() + TRUNCATION_MARKER


def budget_history(
    history: list[dict[str, str]] | None,
    max_chars: int,
) -> list[dict[str, str]]:
    """Keep newest complete user/assistant turns within the history budget."""
    remaining = max(0, int(max_chars))
    valid: list[dict[str, str]] = []
    for turn in history or []:
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        valid.append({"role": role, "content": content})

    groups: list[list[dict[str, str]]] = []
    index = 0
    while index < len(valid):
        current = valid[index]
        if (
            current["role"] == "user"
            and index + 1 < len(valid)
            and valid[index + 1]["role"] == "assistant"
        ):
            groups.append([current, valid[index + 1]])
            index += 2
        else:
            groups.append([current])
            index += 1

    selected_groups: list[list[dict[str, str]]] = []
    for group in reversed(groups):
        if remaining <= 0:
            break
        group_chars = sum(len(turn["content"]) for turn in group)
        if group_chars <= remaining:
            selected_groups.append(group)
            remaining -= group_chars
            continue
        if selected_groups:
            break
        # A single newest turn may itself exceed the budget. Keep both sides
        # of that turn with proportional clipping instead of retaining an
        # orphaned assistant response.
        allocations: list[int] = []
        unallocated = remaining
        for group_index, turn in enumerate(group):
            turns_left = len(group) - group_index
            if turns_left == 1:
                allocation = unallocated
            else:
                allocation = max(
                    1,
                    int(
                        remaining
                        * len(turn["content"])
                        / max(1, group_chars)
                    ),
                )
                allocation = min(
                    allocation,
                    max(1, unallocated - (turns_left - 1)),
                )
            allocations.append(allocation)
            unallocated -= allocation
        clipped_group = [
            {
                "role": turn["role"],
                "content": clip_text(turn["content"], allocation),
            }
            for turn, allocation in zip(group, allocations, strict=True)
            if allocation > 0
        ]
        if clipped_group:
            selected_groups.append(clipped_group)
        break

    selected_groups.reverse()
    return [
        turn
        for group in selected_groups
        for turn in group
        if turn["content"]
    ]

"""Stage 4: deterministic regex + alias matching to a tool call (0 tokens)."""

from __future__ import annotations

import re
from collections.abc import Callable

from server.pipeline.types import ToolCall
from server.tools.aliases import URL_ALIASES

_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUM = r"(\d{1,2}|" + "|".join(_NUM_WORDS) + r")"
_URLISH = re.compile(r"^(?:https?://\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?)$")


def _num(s: str) -> int:
    return _NUM_WORDS.get(s) or int(s)


def _open_target(target: str) -> ToolCall:
    target = target.strip()
    if target in URL_ALIASES or _URLISH.fullmatch(target):
        return ToolCall("open_url", {"url": target})
    return ToolCall("open_app", {"app": target})


Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], ToolCall]]

RULES: list[Rule] = [
    (
        re.compile(
            r"(?:set |change |turn )?(?:the )?(?:volume|vol)(?: level)?(?: to| at)? "
            r"(\d{1,3})%?"
        ),
        lambda m: ToolCall("set_volume", {"level": int(m.group(1))}),
    ),
    (
        re.compile(r"(?:set )?(\d{1,3})% volume"),
        lambda m: ToolCall("set_volume", {"level": int(m.group(1))}),
    ),
    (
        re.compile(r"mute(?: (?:the )?(?:volume|sound|audio))?"),
        lambda m: ToolCall("set_volume", {"level": 0}),
    ),
    (
        re.compile(r"(?:take|grab|capture)? ?(?:a )?screen ?shot"),
        lambda m: ToolCall("take_screenshot"),
    ),
    (
        re.compile(r"(?:close|kill)(?: the)?(?: active| current| this)?(?: window)?|close this"),
        lambda m: ToolCall("close_window"),
    ),
    (
        re.compile(rf"(?:switch to |go to |move to )?(?:workspace|desktop) {_NUM}"),
        lambda m: ToolCall("switch_workspace", {"num": _num(m.group(1))}),
    ),
    (
        re.compile(
            r"(?:what(?:'s| is) (?:my |the )?)?battery(?: level| status| percentage)?"
            r"|how much battery(?: do i have| is left)?"
        ),
        lambda m: ToolCall("get_battery_status"),
    ),
    (
        re.compile(
            r"(?:what(?:'s| is) )?(?:my )?work ?time(?: today)?"
            r"|how (?:long|much) (?:have|did) i (?:been )?work(?:ed|ing)?(?: today)?"
        ),
        lambda m: ToolCall("get_work_time_today"),
    ),
    (
        re.compile(r"git status(?: (?:of|for|in) (?P<repo>\S.*))?"),
        lambda m: ToolCall("get_git_status", {"repo": m.group("repo")} if m.group("repo") else {}),
    ),
    (
        re.compile(r"(?:open|launch|start|run) (?:up )?(?:the )?(?P<t>.+?)(?: app| application)?"),
        lambda m: _open_target(m.group("t")),
    ),
    (
        re.compile(r"(?:focus|focus on|switch to|go to) (?:the )?(?P<c>.+?)(?: window)?"),
        lambda m: ToolCall("focus_window", {"window_class": m.group("c")}),
    ),
]


def match(text: str) -> ToolCall | None:
    for pattern, build in RULES:
        m = pattern.fullmatch(text)
        if m:
            return build(m)
    return None

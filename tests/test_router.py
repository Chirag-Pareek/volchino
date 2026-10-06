"""Test router stage: reply parsing and routing."""

from server.pipeline.cache import Cache
from server.pipeline.router import GroqRouter, parse_reply
from server.tools import REGISTRY


def test_parse_valid():
    call = parse_reply('{"tool":"set_volume","args":{"level":30}}', REGISTRY)
    assert call is not None
    assert call.tool == "set_volume"
    assert call.args == {"level": 30}


def test_parse_needs_reasoning():
    call = parse_reply('{"tool":"needs_reasoning"}', REGISTRY)
    assert call is None


def test_parse_unknown_tool():
    call = parse_reply('{"tool":"nuke_everything","args":{}}', REGISTRY)
    assert call is None


def test_parse_invalid_json():
    call = parse_reply("not json at all", REGISTRY)
    assert call is None


def test_parse_bad_args():
    call = parse_reply('{"tool":"set_volume","args":{"level":999}}', REGISTRY)
    assert call is None


def test_parse_markdown_wrapper():
    call = parse_reply('```json\n{"tool":"set_volume","args":{"level":50}}\n```', REGISTRY)
    assert call is not None
    assert call.args == {"level": 50}


async def test_route_no_client(db):
    cache = Cache(db)
    router = GroqRouter(None, cache, REGISTRY)
    assert router.enabled is False
    assert await router.route("anything") is None

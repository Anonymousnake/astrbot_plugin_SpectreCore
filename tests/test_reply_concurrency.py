"""Offline regression check; real decision/state/handler, fake model and storage.

Run: python tests/test_reply_concurrency.py
Pass --native-lock PATH to execute AstrBot's installed session-lock source too.
No AstrBot startup, provider requests, platform sends or persistent data writes.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import random
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


class Event:
    def __init__(self, group="GROUP_A", sender="MEMBER_A", bot="BOT_A", wake=True):
        self.group, self.sender, self.bot = group, sender, bot
        self.is_at_or_wake_command = wake
        self.unified_msg_origin = f"{bot}:GroupMessage:{group}"
        self.in_scope = True
        self.mode = "spectre"
        self.text = "explicit request" if wake else "ambient message"
        self.extras = {}
        self.saved = self.stopped = self.call_llm = False

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.sender

    def get_self_id(self):
        return self.bot

    def is_private_chat(self):
        return False

    def get_message_outline(self):
        return self.text

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def should_call_llm(self, value):
        self.call_llm = value

    def stop_event(self):
        self.stopped = True


class History:
    @staticmethod
    async def process_and_save_user_message(event):
        event.saved = True


def load_class(path, name, env, methods=None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    cls.decorator_list = []
    if methods is not None:
        cls.body = [n for n in cls.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                    or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in methods)]
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), env)
    return env[name]


async def verify(native_lock_path=None):
    env = dict(logger=logging.getLogger("spectre-concurrency-test"), time=time,
               threading=threading, random=random, Star=object, HistoryStorage=History,
               allowed=lambda ev, cfg: ev.in_scope, group_mode=lambda ev: ev.mode)
    state = load_class(ROOT / "utils/llm_utils.py", "LLMUtils", env,
                       {"get_chat_key", "set_llm_in_progress", "is_llm_in_progress", "get_last_call_time", "clear_call_status"})
    decision = load_class(ROOT / "utils/reply_decision.py", "ReplyDecision", env)
    plugin = load_class(ROOT / "main.py", "SpectreCore", env, {"_process_message"})()
    plugin.config = {"ambient_cooldown_seconds": 60, "enabled_groups": ["GROUP_A", "GROUP_B"],
                     "model_frequency": {"method": "概率回复", "probability": {"probability": 1.0}}}
    plugin.context = None
    requests, delivered, checks = [], [], []
    if native_lock_path:
        native = {}
        exec(compile(native_lock_path.read_text(encoding="utf-8"), str(native_lock_path), "exec"), native)
        acquire_lock = native["session_lock_manager"].acquire_lock
    else:
        # Model the existing downstream lock, not a new plugin queue implementation.
        locks = {}
        acquire_lock = lambda key: locks.setdefault(key, asyncio.Lock())

    async def builder(ev, config, context):
        requests.append(ev)
        return ev

    state.call_llm = staticmethod(builder)
    busy = lambda ev: state.is_llm_in_progress(ev.unified_msg_origin, False, ev.group)
    count = lambda ev: state._llm_call_status[state.get_chat_key(ev.unified_msg_origin, False, ev.group)]["pending_count"]

    async def consume(ev, requested=None, entered=None, release=None, fail=False):
        gen = plugin._process_message(ev)
        try:
            async for request in gen:
                assert request is ev
                if requested:
                    requested.set()
                async with acquire_lock(ev.unified_msg_origin):
                    if entered:
                        entered.set()
                    if release:
                        await release.wait()
                    if fail:
                        raise RuntimeError("controlled downstream failure")
                    delivered.append(ev)
        finally:
            await gen.aclose()

    async def wait(signal):
        await asyncio.wait_for(signal.wait(), 2)

    async def settle():
        # Nested async generators finalize on subsequent event-loop turns after errors.
        for _ in range(16):
            await asyncio.sleep(0)

    a, b, c = Event(), Event(sender="MEMBER_B"), Event()
    a_enter, b_request, b_enter, c_request, c_enter = [asyncio.Event() for _ in range(5)]
    a_release, b_release, c_release = [asyncio.Event() for _ in range(3)]
    ta = asyncio.create_task(consume(a, entered=a_enter, release=a_release))
    await wait(a_enter)
    tb = asyncio.create_task(consume(b, requested=b_request, entered=b_enter, release=b_release))
    await wait(b_request)
    assert b.saved and b.call_llm and b.get_extra("spectrecore_request")
    assert not b.stopped and not b_enter.is_set() and b not in delivered
    assert count(a) == 2
    checks.append("same-group other-member explicit request waits instead of disappearing")
    tc = asyncio.create_task(consume(c, requested=c_request, entered=c_enter, release=c_release))
    await wait(c_request)
    assert count(a) == 3 and not c_enter.is_set()
    checks.append("same-member consecutive explicit requests are retained")

    other_group = Event(group="GROUP_B")
    await consume(other_group)
    assert other_group in delivered and not busy(other_group) and busy(a)
    checks.append("different groups proceed independently")
    other_bot = Event(bot="BOT_B")
    await consume(other_bot)
    assert other_bot in delivered and not busy(other_bot) and count(a) == 3
    checks.append("different bot instances do not share busy state")

    ambient = Event(wake=False)
    plugin.config["ambient_cooldown_seconds"] = 0
    assert not decision.should_reply(ambient, plugin.config)
    checks.append("ambient traffic stays suppressed while requests are active or waiting")
    a_release.set()
    await asyncio.wait_for(ta, 2)
    await wait(b_enter)
    assert count(a) == 2 and busy(a) and not c_enter.is_set()
    assert not decision.should_reply(ambient, plugin.config)
    checks.append("first completion does not clear later requests' busy state")
    b_release.set()
    await asyncio.wait_for(tb, 2)
    await wait(c_enter)
    assert count(a) == 1 and busy(a)
    c_release.set()
    await asyncio.wait_for(tc, 2)
    assert count(a) == 0 and not busy(a)
    assert [ev for ev in delivered if ev in (a, b, c)] == [a, b, c]
    assert all(ev.stopped for ev in (a, b, c))
    checks.append("all three same-session replies complete in arrival order")

    plugin.config["ambient_cooldown_seconds"] = 60
    assert not decision.should_reply(ambient, plugin.config)
    assert decision.should_reply(Event(), plugin.config)
    checks.append("completion cooldown applies only to ambient traffic")
    plugin.config["ambient_cooldown_seconds"] = 0
    assert decision.should_reply(ambient, plugin.config)
    plugin.config["model_frequency"]["probability"]["probability"] = 0.0
    assert not decision.should_reply(ambient, plugin.config)
    assert decision.should_reply(Event(), plugin.config)
    checks.append("configured ambient probability is preserved")

    out = Event()
    out.in_scope = False
    assert not decision.should_reply(out, plugin.config)
    await consume(out)
    assert out not in requests and not out.saved
    checks.append("explicit requests still respect access scope")
    plugin.config["_temp_mute"] = {"until": time.time() + 60}
    assert not decision.should_reply(Event(), plugin.config)
    del plugin.config["_temp_mute"]
    plugin.config["model_frequency"]["blacklist_keywords"] = ["BLOCK_TOKEN"]
    blocked_text = Event()
    blocked_text.text = "BLOCK_TOKEN"
    assert not decision.should_reply(blocked_text, plugin.config)
    checks.append("mute and keyword blacklist are not bypassed by mentions")
    for owner in ("qq_agent_command_handled", "handlers_parsed_params"):
        command = Event()
        command.set_extra(owner, True)
        await consume(command)
        assert command not in requests and not command.saved
    checks.append("registered commands keep event ownership")
    self_event = Event(sender="BOT_A")
    await consume(self_event)
    assert self_event not in requests
    checks.append("bot's own messages do not trigger a reply loop")

    active, waiting = Event(group="GROUP_CANCEL"), Event(group="GROUP_CANCEL", sender="MEMBER_B")
    entered, requested, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    t_active = asyncio.create_task(consume(active, entered=entered, release=release))
    await wait(entered)
    t_wait = asyncio.create_task(consume(waiting, requested=requested))
    await wait(requested)
    assert count(active) == 2
    t_wait.cancel()
    try:
        await t_wait
    except asyncio.CancelledError:
        pass
    await settle()
    assert count(active) == 1 and busy(active)
    checks.append("cancelling a queued request preserves the active request's state")
    t_active.cancel()
    try:
        await t_active
    except asyncio.CancelledError:
        pass
    await settle()
    assert count(active) == 0 and not busy(active)
    await consume(Event(group="GROUP_CANCEL", sender="AFTER_CANCEL"))
    checks.append("active cancellation releases state and native lock for subsequent requests")

    failed = Event(group="GROUP_FAIL")
    try:
        await consume(failed, fail=True)
        raise AssertionError("failure not propagated")
    except RuntimeError as exc:
        assert str(exc) == "controlled downstream failure"
    await settle()
    assert not busy(failed) and failed.stopped
    await consume(Event(group="GROUP_FAIL", sender="AFTER_FAIL"))
    checks.append("downstream model/send failure does not leave a stuck busy state")

    async def broken_builder(ev, config, context):
        raise RuntimeError("controlled builder failure")

    state.call_llm = staticmethod(broken_builder)
    failed_build = Event(group="GROUP_BUILD_FAIL")
    try:
        await consume(failed_build)
        raise AssertionError("builder failure not propagated")
    except RuntimeError as exc:
        assert str(exc) == "controlled builder failure"
    assert not busy(failed_build) and failed_build.stopped
    state.call_llm = staticmethod(builder)
    checks.append("request construction failure balances the pending count")
    assert all(status["pending_count"] == 0 for status in state._llm_call_status.values())
    source_hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                     ("utils/reply_decision.py", "utils/llm_utils.py", "main.py")}
    result = {"ok": True, "checks_passed": len(checks), "checks": checks,
              "source_sha256": source_hashes, "network_requests": 0, "persistent_writes": 0,
              "native_lock_source": "installed/source snapshot" if native_lock_path else "stdlib test double"}
    if native_lock_path:
        result["native_lock_sha256"] = hashlib.sha256(native_lock_path.read_bytes()).hexdigest()
    return result


def test_reply_concurrency():
    assert asyncio.run(verify())["ok"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-lock", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.native_lock)), ensure_ascii=False, indent=2))

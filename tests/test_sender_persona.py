"""Offline routing checks; no model calls, QQ sends or persistent session writes."""

import ast
import asyncio
import copy
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("sender_persona", ROOT / "utils/sender_persona.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

WARM = {"name": "warm", "prompt": "WARM PERSONA", "_begin_dialogs_processed": [{"role": "assistant", "content": "warm example"}]}
BASE = {"name": "base", "prompt": "BASE PERSONA", "_begin_dialogs_processed": [{"role": "assistant", "content": "base example"}]}
RULE = {"enabled": True, "user_id": "12345", "persona_id": "warm", "group_ids": ["90001"]}


def event(sender="12345", group="90001", private=False, platform="qq"):
    return SimpleNamespace(
        is_private_chat=lambda: private, get_group_id=lambda: group,
        get_sender_id=lambda: sender, get_platform_id=lambda: platform,
        get_platform_name=lambda: "test", get_sender_name=lambda: "12345 (spoofed owner)",
        get_self_id=lambda: "99999", unified_msg_origin="qq:GroupMessage:" + group,
        get_message_outline=lambda: "hello", get_extra=lambda *a: None,
        request_llm=lambda **kw: SimpleNamespace(**kw), set_extra=lambda *a: None,
        is_at_or_wake_command=True,
    )


def test_exact_identity_scope_and_invalid_rules():
    cfg = {"platform_id": "qq", "sender_persona_rules": [RULE]}
    choose = lambda ev, conf=cfg: module.select_sender_persona(ev, conf, [BASE, WARM])
    assert choose(event()) == WARM
    for ev in [event("123456"), event("54321"), event(group="90002"), event(private=True), event(platform="other")]:
        assert choose(ev) is None
    for rule in [dict(RULE, enabled=False), dict(RULE, persona_id="deleted"), dict(RULE, group_ids="90001"), dict(RULE, user_id="")]:
        assert choose(event(), dict(cfg, sender_persona_rules=[rule])) is None
    assert choose(event(group="90002"), dict(cfg, sender_persona_rules=[dict(RULE, group_ids=[])])) == WARM
    assert choose(event(), dict(cfg, sender_persona_rules=[None, {}, RULE])) == WARM


def test_native_empty_mention_replaces_only_persona_and_examples():
    original = copy.deepcopy(BASE)
    req = SimpleNamespace(system_prompt="before\n# Persona Instructions\n\nBASE PERSONA\nafter",
                          contexts=copy.deepcopy(BASE["_begin_dialogs_processed"]) + [{"role": "user", "content": "history"}])
    assert module.replace_native_persona(req, BASE, WARM)
    assert "BASE PERSONA" not in req.system_prompt
    assert req.system_prompt.startswith("before") and req.system_prompt.endswith("after")
    assert req.contexts == WARM["_begin_dialogs_processed"] + [{"role": "user", "content": "history"}]
    req.contexts[0]["content"] = "changed"
    assert WARM["_begin_dialogs_processed"][0]["content"] == "warm example"
    assert BASE == original
    req.system_prompt = "unrelated custom request"
    assert not module.replace_native_persona(req, BASE, WARM)


def test_request_personas_do_not_leak_between_senders(monkeypatch):
    # Load the real request builder without starting AstrBot or its plugins.
    tree = ast.parse((ROOT / "utils/llm_utils.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LLMUtils")
    fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "call_llm")
    fn.decorator_list = []
    api = ModuleType("astrbot.api")
    api.sp = SimpleNamespace(get_async=AsyncMock(return_value={"persona_id": "base"}))
    monkeypatch.setitem(sys.modules, "astrbot.api", api)
    quote = SimpleNamespace(get_mode=lambda *a: False)
    env = dict(AstrMessageEvent=object, AstrBotConfig=dict, Context=object, ProviderRequest=object,
               logger=logging.getLogger("test"), copy=copy,
               select_sender_persona=module.select_sender_persona, SENDER_GUIDANCE=module.SENDER_GUIDANCE,
               HistoryStorage=SimpleNamespace(get_history=lambda *a: []), QuoteUtils=quote)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "llm_utils.py", "exec"), env)
    personas = copy.deepcopy([BASE, WARM])
    ctx = SimpleNamespace(persona_manager=SimpleNamespace(personas_v3=personas))
    cfg = {"platform_id": "qq", "sender_persona_rules": [RULE]}
    warm = asyncio.run(env["call_llm"](event(), cfg, ctx))
    normal = asyncio.run(env["call_llm"](event("54321"), cfg, ctx))
    missing = asyncio.run(env["call_llm"](event(), dict(cfg, sender_persona_rules=[dict(RULE, persona_id="deleted")]), ctx))
    assert "WARM PERSONA" in warm.system_prompt and "BASE PERSONA" not in warm.system_prompt
    assert "BASE PERSONA" in normal.system_prompt and "WARM PERSONA" not in normal.system_prompt
    assert "BASE PERSONA" in missing.system_prompt
    assert warm.contexts == WARM["_begin_dialogs_processed"]
    assert normal.contexts == BASE["_begin_dialogs_processed"]
    warm.contexts[0]["content"] = "mutated request"
    assert personas == [BASE, WARM]


def test_native_hook_respects_scope_and_command_ownership():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SpectreCore")
    fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "apply_sender_persona")
    fn.decorator_list = []
    env = dict(AstrMessageEvent=object, ProviderRequest=object, logger=logging.getLogger("test"),
               select_sender_persona=module.select_sender_persona,
               replace_native_persona=module.replace_native_persona, SENDER_GUIDANCE=module.SENDER_GUIDANCE,
               allowed=lambda ev, cfg: ev.get_group_id() == "90001" and ev.get_platform_id() == "qq")
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "main.py", "exec"), env)
    resolve = AsyncMock(return_value=("base", BASE, "base", False))
    plugin = SimpleNamespace(config={"platform_id": "qq", "sender_persona_rules": [RULE]},
                             context=SimpleNamespace(persona_manager=SimpleNamespace(
                                 personas_v3=[BASE, WARM], resolve_selected_persona=resolve),
                                 get_config=lambda **kw: {}))
    request = lambda: SimpleNamespace(system_prompt="\n# Persona Instructions\n\nBASE PERSONA\n",
                                      contexts=copy.deepcopy(BASE["_begin_dialogs_processed"]),
                                      conversation=SimpleNamespace(persona_id="base"))
    ev, req = event(), request()
    asyncio.run(env[fn.name](plugin, ev, req))
    assert "WARM PERSONA" in req.system_prompt and req.conversation.persona_id == "base"
    for ev in [event(private=True), event(group="90002"), event(platform="other")]:
        req = request()
        asyncio.run(env[fn.name](plugin, ev, req))
        assert "WARM PERSONA" not in req.system_prompt
    for owner in ["spectrecore_request", "qq_agent_command_handled", "handlers_parsed_params"]:
        ev, req = event(), request()
        ev.get_extra = lambda key: key == owner
        asyncio.run(env[fn.name](plugin, ev, req))
        assert "WARM PERSONA" not in req.system_prompt

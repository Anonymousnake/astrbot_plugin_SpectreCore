"""Exercise quote cleanup without loading AstrBot or sending QQ messages."""

import ast
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional


class Plain:
    def __init__(self, text):
        self.text = text


class Reply:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


tree = ast.parse((Path(__file__).parents[1] / "utils/quote_utils.py").read_text(encoding="utf-8"))
tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef)]
scope = dict(re=re, Any=Any, Dict=Dict, Optional=Optional, Context=object,
             AstrMessageEvent=object, AstrBotConfig=dict, AstrBotMessage=object,
             MessageEventResult=object, Plain=Plain, Reply=Reply, At=Reply,
             MessageType=SimpleNamespace(FRIEND_MESSAGE="private"), logger=logging.getLogger("test"))
exec(compile(tree, "quote_utils.py", "exec"), scope)
QuoteUtils = scope["QuoteUtils"]
TARGET = {"10": {"id": "real-message-id", "sender_id": "12345", "sender_name": "name", "content": "hello"}}


def apply(text, targets=TARGET, prefix=None):
    ev = SimpleNamespace(get_extra=lambda _: targets, unified_msg_origin="test",
                         get_platform_name=lambda: "test", get_message_type=lambda: "group")
    result = SimpleNamespace(chain=(prefix or []) + [Plain(text)])
    ev.clear_result = lambda: setattr(result, "cleared", True)
    QuoteUtils.apply(ev, result, SimpleNamespace(get_config=lambda **kw: {}))
    return result


def test_model_marker_variants_are_removed_and_resolved():
    for text in ["[引用:10] 正文", "【引用：10】 正文", "引用:10 正文", "  引用 ： 10\n正文"]:
        result = apply(text)
        assert isinstance(result.chain[0], Reply)
        assert result.chain[0].id == "real-message-id"
        assert result.chain[1].text == "正文"


def test_invalid_targets_and_marker_only_messages_do_not_leak():
    for targets in [None, {}, TARGET]:
        result = apply("[引用:999] 正文", targets)
        assert len(result.chain) == 1 and result.chain[0].text == "正文"
    assert apply("[引用:10]").cleared
    assert apply("引用:10").cleared
    result = apply("[引用:10] 正文", prefix=[Reply(id="existing")])
    assert sum(isinstance(c, Reply) for c in result.chain) == 1


def test_normal_prose_is_preserved_and_first_valid_target_wins():
    for text in ["请引用:10 这句话", "引用:10条资料", "引用:10 是示例"]:
        # Without this request's quote table, bare prose is not protocol metadata.
        result = apply(text, None)
        assert result.chain[0].text == text
    result = apply("[引用:999][引用:10] 正文")
    assert result.chain[0].id == "real-message-id"

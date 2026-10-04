"""Run the standalone limiter without importing AstrBot's plugin runtime."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("reply_limits", Path(__file__).parents[1] / "utils/reply_limits.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_bounds_and_markup():
    short = "操，终于他妈跑通了。&&qq_cute&&"
    assert module.limit_reply(short, 60) == short
    assert module.limit_reply("<NO_RESPONSE>", 4) == "<NO_RESPONSE>"
    long = "[引用:12]" + "这是一个完整的句子。" * 20 + "&&qq_mock&&"
    bounded = module.limit_reply(long, 60)
    assert bounded.startswith("[引用:12]") and bounded.endswith("。")
    assert len(module.MARKUP.sub("", bounded)) <= 60
    assert module.limit_reply(long, 0) == long
    for text in ("甲" * 150, "[引用:1]" + "甲&&qq_mock&&乙，" * 40, "第一句。" + "乙" * 100, "x " * 80):
        for limit in (1, 20, 40, 60, 80):
            result = module.limit_reply(text, limit)
            assert len(module.MARKUP.sub("", result)) <= limit
            assert result.count("&&") % 2 == 0
    assert module.limit_reply("操，他妈的真的挺折腾，这回终于跑通了，" + "后话" * 50, 23).endswith("。")

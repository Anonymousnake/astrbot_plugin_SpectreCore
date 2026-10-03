"""Deployment scope is checked before waking, collecting history or replying."""

import sys
from pathlib import Path

from astrbot.api.event import filter


def group_mode(event):
    plugins_dir = str(Path(__file__).resolve().parent.parent)
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)
    try:
        from astrbot_plugin_access_control.dialogue_routing import resolve_group_dialogue_mode
    except ImportError:
        return "blocked"
    return resolve_group_dialogue_mode(event)


def allowed(event, config):
    if not event.is_private_chat():
        return group_mode(event) == "spectre"
    platform = config.get("platform_id", "")
    if platform and event.get_platform_id() != platform:
        return False
    if not config.get("enabled_private", False):
        return False
    if config.get("local_access_control", True):
        plugins_dir = str(Path(__file__).resolve().parent.parent)
        if plugins_dir not in sys.path:
            sys.path.insert(0, plugins_dir)
        try:
            from astrbot_plugin_access_control.access_control import is_plugin_allowed
        except ImportError:
            return False
        return is_plugin_allowed(
            "spectrecore", event, default_allow=False, default_allow_private=False
        )
    return True


class DialogueFilter(filter.CustomFilter):
    config = {}

    def filter(self, event, cfg):
        original = str(getattr(event.message_obj, "message_str", "") or event.message_str)
        if original.lstrip().startswith("/"):
            return False
        if event.is_private_chat():
            return allowed(event, self.config)
        return group_mode(event) != "legacy"

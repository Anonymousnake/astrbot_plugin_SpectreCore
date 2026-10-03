"""Deployment scope is checked before waking, collecting history or replying."""

import sys
from pathlib import Path

from astrbot.api.event import filter


def allowed(event, config):
    platform = config.get("platform_id", "")
    if platform and event.get_platform_id() != platform:
        return False
    if event.is_private_chat():
        if not config.get("enabled_private", False):
            return False
    else:
        group = str(event.get_group_id() or "")
        if not group or group in {str(g) for g in config.get("blocked_groups", [])}:
            return False
        if not config.get("enable_all_groups", False) and group not in {
            str(g) for g in config.get("enabled_groups", [])
        }:
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
        if not allowed(event, self.config):
            return False
        original = str(getattr(event.message_obj, "message_str", "") or event.message_str)
        return not original.lstrip().startswith("/")

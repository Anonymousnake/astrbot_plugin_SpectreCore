# Scoped deployment

This fork integrates with the shared `astrbot_plugin_access_control` plugin.
Keep `local_access_control` enabled and configure an explicit platform/group allowlist.
Commands mark ownership; out-of-scope events never collect history or inject lore.
Real configuration and history belong in AstrBot data directories, never this repository.
Worldbook model writes are disabled; existing long-term memory remains responsible for facts.
Validate with the control repository scoped-dialogue smoke check before deployment.

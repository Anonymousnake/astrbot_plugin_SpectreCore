# Scoped deployment

This fork integrates with the shared `astrbot_plugin_access_control` plugin.
Keep `local_access_control` enabled and configure an explicit platform/group allowlist.
The SpectreCore WebUI group list is the sole group allowlist for dialogue routing.
The shared ACL helper reads the active plugin's metadata/configuration, checks bans and
user restrictions, and lets catchphrases/reply review follow the same per-event decision.
Saving configuration hot-reloads SpectreCore. Removing a group or disabling/unloading
the plugin restores legacy routing for new messages. Private chat remains independent.
`reply_max_chars` bounds visible group replies before memory capture and delivery. The
same cap shortens old bot replies only in the LLM input, leaving stored history and user
messages intact. Zero disables the bound. Quote/meme markup is kept whole and excluded
from the visible-character count. Tool-call responses and private chats are unaffected.
Commands mark ownership; out-of-scope events never collect history or inject lore.
Real configuration and history belong in AstrBot data directories, never this repository.
Worldbook model writes are disabled; existing long-term memory remains responsible for facts.
Validate with the control repository scoped-dialogue smoke check before deployment.

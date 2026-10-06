"""Request-local persona rules; never change the shared group/session binding."""

import copy
import logging

SENDER_GUIDANCE = (
    "\n\n[本轮回复对象]\n请按本轮人格回应当前发言者（QQ {sender_id}）。"
    "聊天记录中对其他人的态度不代表本轮态度，不要沿用上一轮的语气，也不要转而回应其他群友。"
)


def select_sender_persona(event, config, personas):
    """Return the first valid matching persona using trusted event identity.

    Args:
        event: Platform event, not a nickname or quoted message.
        config: Plugin configuration with optional sender_persona_rules.
        personas: AstrBot's current processed persona list.

    Returns:
        Matching persona, or None to retain the existing group persona.
    """
    if event.is_private_chat():
        return None
    if config.get("platform_id") and event.get_platform_id() != config["platform_id"]:
        return None
    rules = config.get("sender_persona_rules", [])
    if not isinstance(rules, list):
        return None
    sender, group = str(event.get_sender_id()), str(event.get_group_id())
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("enabled", True) is not True:
            continue
        user_id = str(rule.get("user_id", "")).strip()
        groups = rule.get("group_ids", [])
        if not user_id.isascii() or not user_id.isdigit() or user_id != sender:
            continue
        if not isinstance(groups, list) or (groups and group not in [str(g).strip() for g in groups]):
            continue
        persona_id = rule.get("persona_id")
        if not isinstance(persona_id, str) or not persona_id.strip():
            continue
        persona = next((p for p in personas if p["name"] == persona_id.strip()), None)
        if persona is not None:
            return persona
        logging.getLogger("astrbot").warning("SpectreCore sender persona missing: %s", persona_id)
    return None


def replace_native_persona(req, original, selected):
    """Replace native persona text/examples while preserving other injections.

    Args:
        req: Native request after AstrBot has assembled persona instructions.
        original: Persona resolved by the native session rules, if any.
        selected: Sender-specific persona for this request.

    Returns:
        Whether the request was updated; unknown prompt layouts are left alone.
    """
    prompt = req.system_prompt or ""
    old_prompt = (original or {}).get("prompt", "")
    new_prompt = selected.get("prompt", "")
    new_block = f"\n# Persona Instructions\n\n{new_prompt}\n" if new_prompt else ""
    if old_prompt:
        old_block = f"\n# Persona Instructions\n\n{old_prompt}\n"
        if old_block not in prompt:
            return False
        prompt = prompt.replace(old_block, new_block, 1)
    else:
        prompt += new_block
    contexts = list(req.contexts or [])
    old_examples = (original or {}).get("_begin_dialogs_processed") or []
    if old_examples:
        if contexts[:len(old_examples)] != old_examples:
            return False
        contexts = contexts[len(old_examples):]
    req.system_prompt = prompt
    req.contexts = copy.deepcopy(selected.get("_begin_dialogs_processed") or []) + contexts
    return True

from astrbot.api.all import *
from astrbot.api.event import filter
from .utils import *
import time
from .local_scope import DialogueFilter, allowed, group_mode
from .utils.reply_limits import limit_reply
from .utils.sender_persona import SENDER_GUIDANCE, replace_native_persona, select_sender_persona
from astrbot.api.provider import ProviderRequest

@register(
    "spectrecore",
    "23q3",
    "使大模型更好的主动回复群聊中的消息，带来生动和沉浸的群聊对话体验",
    "2.3.2",
    "https://github.com/Anonymousnake/astrbot_plugin_SpectreCore"
)
class SpectreCore(Star):
    """
    使大模型更好的主动回复群聊中的消息，带来生动和沉浸的群聊对话体验
    """
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        DialogueFilter.config = config
        # 初始化各个工具类
        HistoryStorage.init(config)
        ImageCaptionUtils.init(context, config)

    @filter.on_llm_request(priority=100000)
    async def apply_sender_persona(self, event: AstrMessageEvent, req: ProviderRequest):
        """Cover native group requests such as the empty-mention greeting."""
        if (event.is_private_chat() or not self.config.get("sender_persona_rules")
                or event.get_extra("spectrecore_request") or not req.conversation
                or event.get_extra("qq_agent_command_handled")
                or event.get_extra("handlers_parsed_params")
                or not allowed(event, self.config)):
            return
        selected = select_sender_persona(event, self.config, self.context.persona_manager.personas_v3)
        if selected is not None:
            _, original, _, _ = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=req.conversation.persona_id,
                platform_name=event.get_platform_name(),
                provider_settings=self.context.get_config(umo=event.unified_msg_origin).get("provider_settings", {}),
            )
            if not replace_native_persona(req, original, selected):
                logger.warning("SpectreCore could not identify native persona block; request unchanged")
                return
            logger.info("SpectreCore native sender persona: group=%s sender=%s persona=%s",
                        event.get_group_id(), event.get_sender_id(), selected["name"])
        req.system_prompt = (req.system_prompt or "") + SENDER_GUIDANCE.format(sender_id=event.get_sender_id())

    @filter.custom_filter(DialogueFilter, priority=-10000)
    async def on_group_message(self, event: AstrMessageEvent):
        """处理群消息喵"""
        try:
            # 保存用户消息到历史记录并尝试回复
            async for result in self._process_message(event):
                yield result
        except Exception as e:
            logger.error(f"处理群消息时发生错误: {e}")

    async def _process_message(self, event: AstrMessageEvent):
        """处理消息的通用逻辑：保存历史记录并尝试回复"""
        if event.get_extra("qq_agent_command_handled"):
            return
        # Registered commands, including commands addressed with @, keep ownership.
        if event.get_extra("handlers_parsed_params"):
            return
        if not event.is_private_chat() and group_mode(event) == "blocked":
            event.should_call_llm(True)
            event.stop_event()
            return
        if not allowed(event, self.config):
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        event.should_call_llm(True)  # Suppress the default fallback, even when silent.
        # 过滤空消息(napcat会发送私聊对方正在输入的状态，导致astrbot识别为空消息)
        message_outline = event.get_message_outline()
        if not message_outline or message_outline.strip() == "":
            logger.debug("收到空消息，忽略处理")
            return

        # 保存用户消息到历史记录
        await HistoryStorage.process_and_save_user_message(event)

        # 尝试自动回复
        try:
            if ReplyDecision.should_reply(event, self.config):
                event.set_extra("spectrecore_request", True)
                async for result in ReplyDecision.process_and_reply(event, self.config, self.context):
                    yield result
        finally:
            event.stop_event()


    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        """处理bot发送的消息喵"""
        if not event.get_extra("spectrecore_request") or not allowed(event, self.config):
            return
        try:           
            # 保存机器人消息
            if event._result and hasattr(event._result, "chain"):
                # 检查是否为重置历史记录的提示消息，如果是则不保存
                message_text = "".join([i.text for i in event._result.chain if hasattr(i, "text")])
                if "已成功重置" in message_text and "的历史记录喵~" in message_text:
                    return
                
                await HistoryStorage.save_bot_message_from_chain(event._result.chain, event)
                logger.debug(f"已保存bot回复消息到历史记录")
                
        except Exception as e:
            logger.error(f"处理bot发送的消息时发生错误: {e}")

    from astrbot.api.provider import LLMResponse
    @filter.on_llm_response(priority=114514)
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """处理大模型回复喵"""
        if not event.get_extra("spectrecore_request") or not allowed(event, self.config):
            return
        logger.debug("SpectreCore received an owned LLM response")
        try:
            if resp.role != "assistant":
                return
            # 只进行文本过滤，不处理读空气逻辑
            resp.completion_text = TextFilter.process_model_text(resp.completion_text, self.config)
            if event.get_extra(QuoteUtils.EXTRA_KEY):
                resp.completion_text = QuoteUtils.normalize_markers(resp.completion_text)
            if resp.completion_text == "<NO_RESPONSE>":
                # Silence is not an assistant utterance for memory/decorators.
                event.stop_event()
            elif not event.is_private_chat() and not (resp.tools_call_name or resp.tools_call_args):
                before = resp.completion_text
                resp.completion_text = limit_reply(before, int(self.config.get("reply_max_chars", 0)))
                if resp.completion_text != before:
                    logger.info("SpectreCore bounded reply: %s -> %s chars", len(before), len(resp.completion_text))
        except Exception as e:
            logger.error(f"处理大模型回复时发生错误: {e}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """在消息发送前处理读空气和模型自主引用喵"""
        if not event.get_extra("spectrecore_request") or not allowed(event, self.config):
            return
        try:
            result = event.get_result()
            if result is None or not result.chain:
                return

            # 检查是否为LLM结果且包含<NO_RESPONSE>标记
            if result.is_llm_result():
                # 获取消息文本内容
                message_text = ""
                for comp in result.chain:
                    if hasattr(comp, 'text'):
                        message_text += comp.text

                # 如果包含<NO_RESPONSE>标记，清空事件结果以阻止消息发送
                if "<NO_RESPONSE>" in message_text:
                    logger.debug(f"检测到读空气标记，阻止消息发送。事件结果: {event.get_result()}")
                    event.clear_result()
                    logger.debug(f"已清空事件结果: {event.get_result()}")
                    return

                # 处理模型自主引用标记
                QuoteUtils.apply(event, result, self.context)

        except Exception as e:
            logger.error(f"处理消息发送前事件时发生错误: {e}")

    @filter.command_group("spectrecore",alias={'sc'})
    def spectrecore(self):
        """插件的前缀喵 可以用sc代替喵"""
        pass

    @spectrecore.command("help", alias=['帮助', 'helpme'])
    async def help(self, event: AstrMessageEvent):
        """查看插件的帮助喵"""
        event.set_extra("qq_agent_command_handled", True)
        event.should_call_llm(True)
        if not allowed(event, self.config):
            event.stop_event()
            yield event.plain_result("这个会话没开新对话功能。")
            return
        help_text = (
            "SpectreCore插件帮助文档\n"
            "使用spectrecore或sc作为指令前缀 如/sc help\n"
            "使用reset指令重置当前聊天记录 如/sc reset\n"
            "   你也可以重置指定群聊天记录 如/sc reset 群号\n"
            "使用history指令可以查看最近聊天记录 如/sc history\n"
            "使用mute/闭嘴指令临时禁用自动回复 如/sc mute 5 或 /sc 闭嘴 10\n"
            "使用unmute/说话指令解除禁用 如/sc unmute 或 /sc 说话"
        )
        platform_name = event.get_platform_name()
        if platform_name in ("qq_official", "qq_official_webhook"):
            help_text += "\n强烈建议前往Github阅读README文档"
        else:
            help_text += "\n↓强烈建议您阅读Github中的README文档↓\nhttps://github.com/23q3/astrbot_plugin_SpectreCore"
        yield event.plain_result(help_text)
    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("history")
    async def history(self, event: AstrMessageEvent, count: int = 10):
        """查看最近的聊天记录喵，默认10条喵，示例/sc history 5"""
        event.set_extra("qq_agent_command_handled", True)
        event.should_call_llm(True)
        if not allowed(event, self.config):
            event.stop_event()
            yield event.plain_result("这个会话没开新对话功能。")
            return
        try:
            # 获取平台名称
            platform_name = event.get_platform_name()
            
            # 判断是群聊还是私聊
            is_private = event.is_private_chat()
            
            # 获取聊天ID
            chat_id = event.get_group_id() if not is_private else event.get_sender_id()
            
            if not chat_id:
                yield event.plain_result("获取聊天ID失败喵，无法显示历史记录")
                return
                
            # 获取历史记录
            history = HistoryStorage.get_history(platform_name, is_private, chat_id)
            
            if not history:
                yield event.plain_result("暂无聊天记录喵")
                return
                
            # 限制记录数量
            if count > 20:
                count = 20  # 限制最大显示数量为20条
            
            # 只取最近的记录
            recent_history = history[-count:] if len(history) > count else history
            
            # 格式化历史记录
            formatted_history = await MessageUtils.format_history_for_llm(recent_history, umo=event.unified_msg_origin)
            
            # 添加标题
            chat_type = "私聊" if is_private else f"群聊({chat_id})"
            title = f"最近{len(recent_history)}条{chat_type}聊天记录喵：\n\n"
            
            # 发送结果
            full_content = title + formatted_history
            
            # 如果内容过长，转为图片发送
            if len(full_content) > 3000:
                image_url = await self.text_to_image(full_content)
                yield event.image_result(image_url)
            else:
                yield event.plain_result(full_content)
            
        except Exception as e:
            logger.error(f"获取历史记录时发生错误: {e}")
            yield event.plain_result(f"获取历史记录失败喵：{str(e)}")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("reset")
    async def reset(self, event: AstrMessageEvent, group_id: str | None = None):
        """重置历史记录喵，不带参数重置当前聊天记录，带群号则重置指定群聊记录 如/sc reset 123456"""
        if group_id and str(group_id) != str(event.get_group_id()):
            yield event.plain_result("请在目标群内重置该群的对话记录。")
            return
        event.set_extra("qq_agent_command_handled", True)
        event.should_call_llm(True)
        if not allowed(event, self.config):
            event.stop_event()
            yield event.plain_result("这个会话没开新对话功能。")
            return
        try:
            # 获取平台名称
            platform_name = event.get_platform_name()
            
            # 判断是否提供了群号
            if group_id:
                # 重置指定群聊的历史记录
                is_private = False
                chat_id = group_id
                chat_type = f"群聊({group_id})"
            else:
                # 判断是群聊还是私聊
                is_private = event.is_private_chat()
                # 获取聊天ID
                chat_id = event.get_group_id() if not is_private else event.get_sender_id()
                chat_type = "私聊" if is_private else f"群聊({chat_id})"
                
                if not chat_id:
                    yield event.plain_result("获取聊天ID失败喵，无法重置历史记录")
                    return
            
            # 先检查是否存在历史记录
            history = HistoryStorage.get_history(platform_name, is_private, chat_id)
            if not history:
                yield event.plain_result(f"{chat_type}没有历史记录喵，无需重置")
                return
                
            # 重置历史记录
            success = HistoryStorage.clear_history(platform_name, is_private, chat_id)
            
            if success:
                yield event.plain_result(f"已成功重置{chat_type}的历史记录喵~")
            else:
                yield event.plain_result(f"重置{chat_type}的历史记录失败喵，可能发生错误")
                
        except Exception as e:
            logger.error(f"重置历史记录时发生错误: {e}")
            yield event.plain_result(f"重置历史记录失败喵：{str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("mute", alias=['闭嘴', 'shutup'])
    async def mute(self, event: AstrMessageEvent, minutes: int = 5):
        """临时禁用自动回复，默认5分钟喵，示例/sc mute 10 或 /sc 闭嘴 3"""
        event.set_extra("qq_agent_command_handled", True)
        event.should_call_llm(True)
        if not allowed(event, self.config):
            event.stop_event()
            yield event.plain_result("这个会话没开新对话功能。")
            return
        try:
            # 计算禁用结束时间
            mute_until = time.time() + (minutes * 60)
            
            # 保存到配置中
            if "_temp_mute" not in self.config:
                self.config["_temp_mute"] = {}
            
            self.config["_temp_mute"]["until"] = mute_until
            self.config["_temp_mute"]["by"] = event.get_sender_id()
            self.config["_temp_mute"]["at"] = time.time()
            
            # 保存配置
            self.config.save_config()
            
            yield event.plain_result(f"好的喵，我会安静 {minutes} 分钟的~")
            
        except Exception as e:
            logger.error(f"执行闭嘴指令时发生错误: {e}")
            yield event.plain_result(f"执行闭嘴指令失败喵：{str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("unmute", alias=['说话', 'speak'])
    async def unmute(self, event: AstrMessageEvent):
        """解除禁用自动回复喵"""
        event.set_extra("qq_agent_command_handled", True)
        event.should_call_llm(True)
        if not allowed(event, self.config):
            event.stop_event()
            yield event.plain_result("这个会话没开新对话功能。")
            return
        try:
            # 检查是否处于静默状态
            mute_info = self.config.get("_temp_mute", {})
            if not mute_info or mute_info.get("until", 0) <= time.time():
                yield event.plain_result("我现在本来就在正常说话喵~")
                return
            
            # 解除静默
            if "_temp_mute" in self.config:
                del self.config["_temp_mute"]
                self.config.save_config()
            
            yield event.plain_result("好耶！我又可以说话了喵~")
            
        except Exception as e:
            logger.error(f"解除闭嘴时发生错误: {e}")
            yield event.plain_result(f"解除闭嘴失败喵：{str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("callllm")
    async def callllm(self, event: AstrMessageEvent):
        """触发一次大模型回复 这是用来开发中测试的喵"""
        event.set_extra("spectrecore_request", True)
        event.set_extra("qq_agent_command_handled", True)
        event.should_call_llm(True)
        if not allowed(event, self.config):
            event.stop_event()
            yield event.plain_result("这个会话没开新对话功能。")
            return
        try:
            # 调用LLM工具类的方法构建并返回请求
            yield await LLMUtils.call_llm(event, self.config, self.context)
        except Exception as e:
            logger.error(f"调用大模型时发生错误: {e}")
            yield event.plain_result(f"触发大模型回复失败喵：{str(e)}")

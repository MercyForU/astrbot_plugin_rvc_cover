"""
astrbot_plugin_rvc_cover - 基于 RVCSVC-API（直链模式）的 AI 翻唱插件

流程：jkapi.com 按歌名搜出音频直链 → RVCSVC-API 直链下载 → UVR5 人声分离 + RVC/SVC 变声 → 混音导出
"""

import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig

from .api import JKApiMusicAPI


@register(
    "astrbot_plugin_rvc_cover",
    "RVC-Cover",
    "基于 RVCSVC-API 的 AI 翻唱插件（直链模式）：jkapi 搜索直链 + UVR5 分离 + 变声一体化",
    "1.1.0",
    "https://github.com/MercyForU/astrbot_plugin_rvc_cover",
)
class RVCSvcPluginV2(Star):
    """RVCSVC-API 翻唱插件（直链模式）"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # RVCSVC-API 配置（后端只接受音频下载直链）
        self.base_url = config.get("rvc_base_url", "http://127.0.0.1:7860").rstrip("/")
        self.default_model = config.get("default_model", "")
        # 混响强度：0(干声) ~ 10(宏大)，默认 4（后端"默认值"）
        self.reverb_intensity = config.get("reverb_intensity", 4)
        # 回声(延迟)效果：0(关闭) ~ 10(明显)，默认 0
        self.delay_intensity = config.get("delay_intensity", 0)
        # 歌曲人声升降调（半音），默认 0
        self.default_key_shift = int(config.get("default_key_shift", 0))
        # 调节人声音量（dB），默认 0
        self.default_vocal_vol = int(config.get("default_vocal_vol", 0))
        # 调节伴奏音量（dB），默认 0
        self.default_inst_vol = int(config.get("default_inst_vol", 0))
        # 人声分离模型 (UVR)
        self.uvr_choice = config.get("uvr_choice", "VR-HP5 (默认)")
        # F0 音高提取算法：rmvpe / pm / fcpe
        self.f0_method = (config.get("f0_method", "rmvpe") or "rmvpe").lower()
        # 自适应变调：启用后，手动升降调为 0 时由后端根据纯人声+舒适区音域自动计算
        self.adaptive_shift = bool(config.get("adaptive_shift", True))
        # 舒适区音域（音名，如 A3 / C4）
        self.comfort_low = config.get("comfort_low", "A3") or "A3"
        self.comfort_high = config.get("comfort_high", "A5") or "A5"
        # 清唱静音修剪：保留的自然间隙（ms）——剪掉大段间奏后句间保留的停顿
        self.trim_keep_gap_ms = int(config.get("trim_keep_gap_ms", 1000))

        # jkapi.com 搜索配置（唯一音源，搜出音频直链喂给后端）
        self.jkapi_key = config.get("jkapi_api_key", "")
        # 平台：qq=QQ音乐（默认），wy=网易云音乐
        self.jkapi_plat = (config.get("jkapi_plat", "qq") or "qq").lower()
        self.search_timeout = config.get("search_timeout", 15)

        # 输出配置
        # 会话上下文（/cover、LLM 工具等带 event 的调用）：输出到 AstrBot 会话工作区
        #   data/workspaces/_FriendMessage_{QQ号}/rvc_cover 或
        #   data/workspaces/_GroupMessage_{群号}/rvc_cover（见 _get_session_output_dir）
        # 无会话上下文（单元测试/CLI）：固定输出到 AstrBot 插件数据目录
        #   data/plugin_data/astrbot_plugin_rvc_cover/（StarTools.get_data_dir，可迁移可审计）
        # 若 output_dir 配置为绝对路径，则全局固定使用该目录。
        _raw_output = config.get("output_dir", "output")
        if os.path.isabs(_raw_output):
            self.output_dir = Path(_raw_output)
            self._output_dir_fixed = True
        else:
            # 非会话上下文的固定输出目录：AstrBot 官方插件数据目录
            # data/plugin_data/astrbot_plugin_rvc_cover/（可迁移可审计，不污染插件安装目录）
            try:
                from astrbot.core.star.star_tools import StarTools
                self.output_dir = StarTools.get_data_dir("astrbot_plugin_rvc_cover")
            except Exception:
                # StarTools 不可用（非 AstrBot 环境，如单元测试）：回退到插件目录 output
                self.output_dir = Path(__file__).resolve().parent / "output"
            self._output_dir_fixed = False
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 插件数据目录可能只读（如 Program Files），回退到插件目录下的 output
            self.output_dir = Path(__file__).resolve().parent / "output"
            self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[RVC-Cover] 输出目录: {'固定 ' + str(self.output_dir) if self._output_dir_fixed else '按会话动态（data/workspaces/.../rvc_cover）'}")

        # 请求超时
        self.request_timeout = config.get("request_timeout", 300)

        # LLM 工具超时设置
        self.llm_tool_timeout = config.get("llm_tool_timeout", 300)

        # jkapi API 实例（按 plat 懒加载）
        self._api_cache: Dict[str, JKApiMusicAPI] = {}
        self._default_api = self._create_api(self.jkapi_plat)
        logger.info(
            f"[RVC-Cover] 初始化完成: base={self.base_url} "
            f"default_model={self.default_model or '未设置'} "
            f"plat={self.jkapi_plat} jkapi_key={'已配置' if self.jkapi_key else '未配置'}"
        )

    # ==================== LLM 工具注册 ====================

    @filter.llm_tool(name="search_music")
    async def llm_search_music(
        self, event: AstrMessageEvent, keyword: str, platform: Optional[str] = None
    ) -> str:
        '''搜索音乐（jkapi.com，返回含音频直链的匹配结果）

        Args:
            keyword(string): 搜索关键词，可以是歌曲名、歌手名等
            platform(string, optional): 音乐平台 'qq'（QQ音乐，默认）或 'wy'（网易云音乐），不指定用配置默认

        Returns:
            搜索到的歌曲（歌名/歌手/直链）
        '''
        return await self._llm_search_music(keyword, platform)

    @filter.llm_tool(name="rvc_cover")
    async def llm_rvc_cover(
        self,
        event: AstrMessageEvent,
        song_name: str,
        artist_name: Optional[str] = None,
        model_name: Optional[str] = None,
        key_shift: int = 0,
        music_source: Optional[str] = None,
        a_capella: bool = False,
    ) -> str:
        '''使用 RVCSVC-API 翻唱歌曲（jkapi 搜索直链 + UVR5 人声分离 + 变声一体化）

        Args:
            song_name(string): 要翻唱的歌曲名称
            artist_name(string, optional): 歌手名称（有指定版本时务必填入）
            model_name(string, optional): 模型名称（支持模糊匹配），优先级高于默认模型
            key_shift(number): 音调调整，范围-12到12，默认0
            music_source(string, optional): 音乐平台 'qq'（QQ音乐）或 'wy'（网易云），不指定用配置默认
            a_capella(boolean, optional): 清唱模式。True=只输出纯人声（不合成伴奏）；False=正常翻唱（人声+伴奏）。用户说"清唱/无伴奏/只要人声"时务必传 True。

        Returns:
            翻唱结果（成功返回音频路径，失败返回错误信息）
        '''
        return await self._llm_rvc_cover(
            song_name=song_name,
            artist_name=artist_name,
            model_name=model_name,
            key_shift=key_shift,
            music_source=music_source,
            a_capella=a_capella,
            event=event,
        )

    @filter.llm_tool(name="smart_cover")
    async def llm_smart_cover(
        self,
        event: AstrMessageEvent,
        song_name: str = None,
        model_name: Optional[str] = None,
        key_shift: int = 0,
    ) -> str:
        '''智能翻唱（自动搜索直链 + 翻唱）

        Args:
            song_name(string): 歌曲名称。若不填，LLM 需从上下文推断
            model_name(string, optional): 模型名称
            key_shift(number): 升降调（-12 到 12），默认 0

        Returns:
            翻唱结果
        '''
        if not song_name:
            msg = event.message_str.replace("/cover", "").replace("/翻唱", "").strip()
            for kw in ["/cover", "/翻唱", "翻唱", "cover"]:
                msg = msg.replace(kw, "").strip()
            if msg:
                song_name = msg
        if not song_name:
            return "请提供歌曲名称"
        return await self._smart_cover_process(song_name, model_name, key_shift, event=event)

    @filter.llm_tool(name="a_capella_cover")
    async def llm_a_capella_cover(
        self,
        event: AstrMessageEvent,
        song_name: str,
        artist_name: Optional[str] = None,
        model_name: Optional[str] = None,
        key_shift: int = 0,
        music_source: Optional[str] = None,
    ) -> str:
        '''清唱模式翻唱：只输出 AI 变声后的纯人声，不合成伴奏（无背景音乐，仅人声 + 混响/延迟效果）

        用户明确要求"清唱"、"只要人声"、"无伴奏"时使用本工具，不要调用 rvc_cover。

        Args:
            song_name(string): 要清唱的歌曲名称
            artist_name(string, optional): 歌手名称（有指定版本时务必填入）
            model_name(string, optional): 模型名称（支持模糊匹配），优先级高于默认模型
            key_shift(number): 音调调整，范围-12到12，默认0
            music_source(string, optional): 音乐平台 'qq'（QQ音乐）或 'wy'（网易云），不指定用配置默认

        Returns:
            清唱结果（成功返回纯人声音频路径，失败返回错误信息）
        '''
        return await self._llm_a_capella_cover(
            song_name=song_name,
            artist_name=artist_name,
            model_name=model_name,
            key_shift=key_shift,
            music_source=music_source,
            event=event,
        )

    # ==================== LLM 工具内部实现 ====================

    async def _llm_search_music(self, keyword: str, platform: Optional[str] = None) -> str:
        """搜索音乐 - LLM 工具内部实现（jkapi，返回 1 条最匹配结果）"""
        try:
            plat = (platform or self.jkapi_plat).lower()
            api = self._create_api(plat)

            songs = await asyncio.wait_for(
                api.fetch_data(keyword=keyword, limit=5),
                timeout=self.search_timeout,
            )

            if not songs:
                reason = "未配置 jkapi_api_key" if not self.jkapi_key else "API 风控 / 歌曲不存在 / 网络问题"
                return f"❌ 在{plat}平台未找到《{keyword}》。可能原因：{reason}"

            s = songs[0]
            lines = [
                f"🎵 找到：《{s.get('name', '未知')}》 - {s.get('artists', '未知')}",
                f"来源: jkapi-{plat}，已获取音频直链",
                f"要翻唱请直接调用 rvc_cover（song_name='{s.get('name', keyword)}'，music_source='{plat}'）",
                f"用户要求清唱时：调用 rvc_cover 并传 a_capella=True（或调用 a_capella_cover 工具）",
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"LLM搜索音乐失败: {traceback.format_exc()}")
            return f"搜索歌曲时出错: {e}"

    async def _llm_rvc_cover(
        self,
        song_name: str,
        artist_name: Optional[str] = None,
        model_name: Optional[str] = None,
        key_shift: int = 0,
        music_source: Optional[str] = None,
        a_capella: bool = False,
        event: Optional["AstrMessageEvent"] = None,
    ) -> str:
        """RVC翻唱 - LLM 工具内部实现（jkapi 搜直链 → 后端直链翻唱）

        a_capella=True 时走清唱通道：后端只输出纯人声，不合成伴奏。
        """
        try:
            model = model_name or self.default_model
            plat = (music_source or self.jkapi_plat).lower()
            api = self._create_api(plat)

            # 1. 搜出音频直链（支持 artist_name 精确匹配）
            search_kw = f"{song_name} {artist_name}" if artist_name else song_name
            songs = []
            try:
                songs = await asyncio.wait_for(
                    api.fetch_data(keyword=search_kw, limit=5),
                    timeout=self.search_timeout,
                )
            finally:
                try:
                    await api.close()
                except Exception:
                    pass

            if not songs or not songs[0].get("url"):
                reason = "未配置 jkapi_api_key" if not self.jkapi_key else "API 未返回直链"
                return f"未找到《{song_name}》的音频直链（{plat}平台，{reason}）"

            song = songs[0]
            direct_link = song["url"].strip()
            display_name = song.get("name") or song_name

            # 2. 调用后端直链翻唱 API（清唱模式时跳过伴奏合成）
            result = await self._call_convert_api(
                display_name, model, key_shift, direct_link, a_capella=a_capella, event=event
            )
            prefix = "清唱" if a_capella else "翻唱"
            suffix_note = "（纯人声，无伴奏）" if a_capella else ""
            if result.get("success"):
                # 把切分后的所有 part 路径都告诉 LLM，让它按顺序发语音
                parts = result.get("parts", [result.get("output_path", "")])
                part_list = "\n".join(
                    [f"  第{i+1}段: {p}" for i, p in enumerate(parts)]
                )
                if len(parts) > 1:
                    return (
                        f"✅ {prefix}完成: 《{display_name}》{suffix_note}\n"
                        f"完整文件: {result.get('output_path', '')}\n"
                        f"已按 {len(parts)} 段切分（QQ语音 1 分钟上限）。\n"
                        f"⚠️ 发送规则：必须**严格按顺序**逐条发送，**每条之间等待 2 秒**再发下一条"
                        f"（连续快发会导致 QQ 只发出第一条，后续全部失败）。"
                        f"等待期间不要发其它消息。\n"
                        f"{part_list}"
                    )
                return (
                    f"✅ {prefix}完成: 《{display_name}》{suffix_note}\n"
                    f"输出文件: {result.get('output_path', '')}（时长未超 1 分钟，直接发送即可）"
                )
            return f"❌ {prefix}失败: {result.get('error', '未知错误')}"
        except Exception as e:
            logger.error(f"LLM翻唱失败: {traceback.format_exc()}")
            return f"翻唱失败: {e}"

    async def _llm_a_capella_cover(
        self,
        song_name: str,
        artist_name: Optional[str] = None,
        model_name: Optional[str] = None,
        key_shift: int = 0,
        music_source: Optional[str] = None,
        event: Optional["AstrMessageEvent"] = None,
    ) -> str:
        """清唱模式 - LLM 工具内部实现（只出纯人声，不合成伴奏）"""
        try:
            model = model_name or self.default_model
            plat = (music_source or self.jkapi_plat).lower()
            api = self._create_api(plat)

            # 1. 搜出音频直链（支持 artist_name 精确匹配）
            search_kw = f"{song_name} {artist_name}" if artist_name else song_name
            songs = []
            try:
                songs = await asyncio.wait_for(
                    api.fetch_data(keyword=search_kw, limit=5),
                    timeout=self.search_timeout,
                )
            finally:
                try:
                    await api.close()
                except Exception:
                    pass

            if not songs or not songs[0].get("url"):
                reason = "未配置 jkapi_api_key" if not self.jkapi_key else "API 未返回直链"
                return f"未找到《{song_name}》的音频直链（{plat}平台，{reason}）"

            song = songs[0]
            direct_link = song["url"].strip()
            display_name = song.get("name") or song_name

            # 2. 调用清唱通道（_llm_rvc_cover a_capella=True，返回结果字符串）
            return await self._llm_rvc_cover(
                song_name=song_name,
                artist_name=artist_name,
                model_name=model_name,
                key_shift=key_shift,
                music_source=music_source,
                a_capella=True,
                event=event,
            )
        except Exception as e:
            logger.error(f"LLM清唱失败: {traceback.format_exc()}")
            return f"清唱失败: {e}"

    async def _smart_cover_process(
        self,
        song_name: str,
        model_name: Optional[str] = None,
        key_shift: int = 0,
        event: Optional["AstrMessageEvent"] = None,
    ) -> str:
        """智能翻唱流程"""
        return await self._llm_rvc_cover(
            song_name=song_name,
            model_name=model_name,
            key_shift=key_shift,
            event=event,
        )

    # ==================== 通用结果发送 ====================

    async def _send_cover_result(
        self,
        event: AstrMessageEvent,
        result: Dict[str, Any],
        display_name: str,
        prefix: str = "翻唱",
    ):
        """按段发送翻唱结果（QQ 语音 1 分钟上限，必须按顺序，每段间隔 2s）"""
        if result.get("success"):
            parts = result.get("parts")
            if parts and len(parts) > 1:
                total = len(parts)
                yield event.plain_result(f"🎵 {prefix}完成，正在按顺序发送 {total} 段语音（间隔 2 秒）...")
                for i, p in enumerate(parts):
                    if os.path.exists(p):
                        yield event.image(p, text=f"第 {i+1}/{total} 段")
                    else:
                        yield event.plain_result(f"⚠️ 第{i+1}段文件缺失: {p}")
                    # 除最后一段外，每段之间等 2 秒再发下一条，避免 QQ 只发第一条
                    if i < total - 1:
                        await asyncio.sleep(2)
                yield event.plain_result(f"✅ 全部 {total} 段已发送完成")
            else:
                # 单段（<=60s）或回退：整文件
                output_path = result.get("output_path")
                if output_path and os.path.exists(output_path):
                    yield event.image(
                        output_path, text=f"🎵 {prefix}完成: 《{display_name}》"
                    )
                else:
                    yield event.plain_result(f"✅ {prefix}完成: {result.get('message', '')}")
        else:
            error_msg = result.get("error", "未知错误")
            logger.error(f"{prefix}失败: {error_msg}")
            yield event.plain_result(f"❌ {prefix}失败: {error_msg}")

    # ==================== 核心翻唱命令 ====================

    @filter.command("cover")
    @filter.command("翻唱")
    async def cover(self, event: AstrMessageEvent):
        """按歌名翻唱：jkapi 搜索出音频直链 → 后端直链下载 + UVR5 分离 + 变声

        用法：
        /cover 歌名              # 默认平台（qq）+ 默认模型翻唱
        /cover 歌名 -p wy       # 用网易云平台搜索
        /cover 歌名 -m 模型名   # 指定模型
        /cover 歌名 -k 12       # 升调 12 半音
        """
        try:
            args = self._parse_cover_args(event.message_str)
            song_name = args.get("song", "")
            model = args.get("model", self.default_model)
            key_shift = args.get("key_shift", self.default_key_shift)
            plat = args.get("platform", self.jkapi_plat)

            if not song_name:
                yield event.plain_result("用法：/cover 歌名 [-p qq|wy] [-m 模型] [-k 升降调]")
                return

            if not self.jkapi_key:
                yield event.plain_result("❌ 未配置 jkapi_api_key，请先在插件配置中填写")
                return

            # 1. 搜索歌曲直链
            plat_name = "QQ音乐" if plat == "qq" else "网易云音乐"
            yield event.plain_result(f"🔍 正在{plat_name}搜索歌曲: {song_name}...")
            api = self._create_api(plat)
            songs = []
            try:
                songs = await asyncio.wait_for(
                    api.fetch_data(keyword=song_name, limit=5),
                    timeout=self.search_timeout,
                )
            finally:
                try:
                    await api.close()
                except Exception:
                    pass

            if not songs or not songs[0].get("url"):
                yield event.plain_result(
                    f"❌ 未在{plat_name}找到《{song_name}》（或 API 未返回直链）。请确认歌名、切换平台（-p qq|wy）或换一首。"
                )
                return

            song = songs[0]
            direct_link = song["url"].strip()
            display_name = song.get("name") or song_name
            logger.info(f"找到歌曲: {display_name} - {song.get('artists')}（{plat}平台直链）")

            # 2. 调用翻唱 API（后端下载直链 + UVR5 分离 + RVC 推理）
            yield event.plain_result(
                f"🎤 正在翻唱《{display_name}》（模型: {model or '默认'}，升降调: {key_shift}）..."
            )
            result = await self._call_convert_api(
                display_name, model, key_shift, direct_link, event=event
            )

            async for msg in self._send_cover_result(event, result, display_name):
                yield msg

        except Exception as e:
            logger.error(f"翻唱处理失败: {traceback.format_exc()}")
            yield event.plain_result(f"❌ 翻唱处理失败: {str(e)}")

    @filter.command("linkcover")
    @filter.command("直链翻唱")
    async def link_cover(self, event: AstrMessageEvent):
        """使用音乐下载直链直接翻唱（跳过搜索）

        用法：
        /linkcover 直链URL            # 直接翻唱（默认模型）
        /linkcover 直链URL -m 模型 -k 12

        注意：直链为可直接下载音频的 URL（mp3/wav/flac 等），需可匿名访问。
        """
        try:
            args = self._parse_cover_args(event.message_str)
            direct_link = args.get("song", "").strip()
            model = args.get("model", self.default_model)
            key_shift = args.get("key_shift", self.default_key_shift)

            if not direct_link:
                yield event.plain_result("用法：/linkcover 音乐直链URL [-m 模型] [-k 升降调]")
                return
            if not direct_link.startswith(("http://", "https://")):
                yield event.plain_result("❌ 直链必须以 http:// 或 https:// 开头")
                return

            link_name = Path(direct_link.split("?")[0].rstrip("/")).name or "直链歌曲"
            yield event.plain_result(
                f"🎤 正在翻唱直链音频 {direct_link}（模型: {model or '默认'}，升降调: {key_shift}）..."
            )
            result = await self._call_convert_api(link_name, model, key_shift, direct_link, event=event)

            async for msg in self._send_cover_result(event, result, link_name, prefix="直链翻唱"):
                yield msg

        except Exception as e:
            logger.error(f"直链翻唱失败: {traceback.format_exc()}")
            yield event.plain_result(f"❌ 直链翻唱失败: {str(e)}")

    @filter.command("capella")
    @filter.command("清唱")
    async def capella(self, event: AstrMessageEvent):
        """按歌名清唱：只输出 AI 变声后的纯人声，不合成伴奏

        用法：
        /capella 歌名              # 默认平台（qq）+ 默认模型清唱
        /capella 歌名 -p wy       # 用网易云平台搜索
        /capella 歌名 -m 模型名   # 指定模型
        /capella 歌名 -k 12       # 升调 12 半音
        """
        try:
            args = self._parse_cover_args(event.message_str)
            song_name = args.get("song", "")
            model = args.get("model", self.default_model)
            key_shift = args.get("key_shift", self.default_key_shift)
            plat = args.get("platform", self.jkapi_plat)

            if not song_name:
                yield event.plain_result("用法：/capella 歌名 [-p qq|wy] [-m 模型] [-k 升降调]")
                return

            if not self.jkapi_key:
                yield event.plain_result("❌ 未配置 jkapi_api_key，请先在插件配置中填写")
                return

            # 1. 搜索歌曲直链
            plat_name = "QQ音乐" if plat == "qq" else "网易云音乐"
            yield event.plain_result(f"🔍 正在{plat_name}搜索歌曲: {song_name}...")
            api = self._create_api(plat)
            songs = []
            try:
                songs = await asyncio.wait_for(
                    api.fetch_data(keyword=song_name, limit=5),
                    timeout=self.search_timeout,
                )
            finally:
                try:
                    await api.close()
                except Exception:
                    pass

            if not songs or not songs[0].get("url"):
                yield event.plain_result(
                    f"❌ 未在{plat_name}找到《{song_name}》（或 API 未返回直链）。请确认歌名、切换平台（-p qq|wy）或换一首。"
                )
                return

            song = songs[0]
            direct_link = song["url"].strip()
            display_name = song.get("name") or song_name
            logger.info(f"清唱找到歌曲: {display_name} - {song.get('artists')}（{plat}平台直链）")

            # 2. 调用清唱通道（后端跳过伴奏合成）
            yield event.plain_result(
                f"🎤 正在清唱《{display_name}》（纯人声，模型: {model or '默认'}，升降调: {key_shift}）..."
            )
            result = await self._call_convert_api(
                display_name, model, key_shift, direct_link, a_capella=True, event=event
            )

            async for msg in self._send_cover_result(
                event, result, display_name, prefix="清唱"
            ):
                yield msg

        except Exception as e:
            logger.error(f"清唱处理失败: {traceback.format_exc()}")
            yield event.plain_result(f"❌ 清唱处理失败: {str(e)}")

    @filter.command("linkcapella")
    @filter.command("直链清唱")
    async def link_capella(self, event: AstrMessageEvent):
        """使用音乐下载直链直接清唱（跳过搜索）

        用法：
        /linkcapella 直链URL            # 直接清唱（默认模型）
        /linkcapella 直链URL -m 模型 -k 12
        """
        try:
            args = self._parse_cover_args(event.message_str)
            direct_link = args.get("song", "").strip()
            model = args.get("model", self.default_model)
            key_shift = args.get("key_shift", self.default_key_shift)

            if not direct_link:
                yield event.plain_result("用法：/linkcapella 音乐直链URL [-m 模型] [-k 升降调]")
                return
            if not direct_link.startswith(("http://", "https://")):
                yield event.plain_result("❌ 直链必须以 http:// 或 https:// 开头")
                return

            link_name = Path(direct_link.split("?")[0].rstrip("/")).name or "直链歌曲"
            yield event.plain_result(
                f"🎤 正在清唱直链音频 {direct_link}（纯人声，模型: {model or '默认'}，升降调: {key_shift}）..."
            )
            result = await self._call_convert_api(
                link_name, model, key_shift, direct_link, a_capella=True, event=event
            )

            async for msg in self._send_cover_result(
                event, result, link_name, prefix="直链清唱"
            ):
                yield msg

        except Exception as e:
            logger.error(f"直链清唱失败: {traceback.format_exc()}")
            yield event.plain_result(f"❌ 直链清唱失败: {str(e)}")

    @filter.command("coverhelp")
    @filter.command("翻唱帮助")
    async def cover_help(self, event: AstrMessageEvent):
        """显示翻唱帮助信息"""
        plat_name = "QQ音乐" if self.jkapi_plat == "qq" else "网易云音乐"
        help_text = """
🎤 **RVC-SVC 翻唱插件（直链模式）**

**基本用法：**
- `/cover 歌名` - 在默认平台（{plat}）搜索歌曲直链并翻唱
- `/cover 歌名 -p qq|wy` - 指定平台搜索（qq=QQ音乐，wy=网易云）
- `/linkcover 直链URL` - 用音频下载直链直接翻唱（跳过搜索）
- `/capella 歌名` - 清唱（只出纯人声，不合成伴奏），参数同 /cover
- `/linkcapella 直链URL` - 用直链直接清唱（跳过搜索）

**LLM 对话：**
直接对机器人说"翻唱XXX"走正常翻唱通道；说"清唱XXX""只要人声""无伴奏XXX"
会调用 a_capella_cover 工具，只输出 AI 变声后的纯人声（保留混响/延迟效果）。

**参数选项：**
- `-m 模型名` 或 `--model 模型名` - 指定翻唱模型
- `-k 升降调` 或 `--key 升降调` - 升降调半音数（如 -k 12 升八度）
- `-p qq|wy` 或 `--plat qq|wy` - 搜索平台（仅 /cover）

**示例：**
- `/cover 晴天` - 在默认平台翻唱晴天
- `/cover 晴天 -m wuxiV1.pth` - 使用 wuxiV1 模型翻唱
- `/cover 晴天 -k 12` - 升调翻唱
- `/cover 晴天 -p wy` - 用网易云平台搜索
- `/linkcover https://cdn.xxx.com/song.mp3 -m wuxiV1.pth` - 用直链翻唱

**语音发送说明：**
QQ 语音消息有 1 分钟上限。长音频会自动按**句子停顿处**切成
50-59 秒的小段（`{output_dir}/{歌名}_parts/partN.mp3`），并**按顺序**逐段发送，
不会切进完整的一句。LLM 工具返回结果也会列出所有分段路径，供模型按顺序发语音。

**输出目录：**
输出文件按**发起会话**归档到 AstrBot 的工作区目录：
- 私信：`data/workspaces/_FriendMessage_{你的QQ号}/rvc_cover/`
- 群聊：`data/workspaces/_GroupMessage_{群号}/rvc_cover/`
若插件配置了绝对路径的 `output_dir`，则全局固定使用该目录。

**当前配置：**
- 后端地址: {base_url}
- 默认模型: {default_model}
- 默认搜索平台: {plat}
- 人声分离模型 (UVR): {uvr}
- F0 音高提取: {f0}
- 自适应变调: {adaptive_status}（舒适区 {comfort}）
- 清唱静音间隙: {gap_ms} ms
- 混响强度: {reverb} / 回声: {delay}
- 默认升降调: {kshift} / 人声音量: {vvol} dB / 伴奏音量: {ivol} dB
- jkapi 密钥: {key_status}
- 输出目录: {output_dir}
""".format(
            plat=self.jkapi_plat,
            base_url=self.base_url,
            default_model=self.default_model or "未设置",
            plat_name=plat_name,
            key_status="已配置" if self.jkapi_key else "未配置",
            output_dir=str(self._resolve_output_dir(event)),
            uvr=self.uvr_choice,
            f0=self.f0_method,
            adaptive_status="开启" if self.adaptive_shift else "关闭",
            comfort=f"{self.comfort_low}-{self.comfort_high}",
            gap_ms=self.trim_keep_gap_ms,
            reverb=self.reverb_intensity,
            delay=self.delay_intensity,
            kshift=self.default_key_shift,
            vvol=self.default_vocal_vol,
            ivol=self.default_inst_vol,
        )
        yield event.plain_result(help_text.strip())

    # ==================== 模型管理命令 ====================

    @filter.command("model")
    @filter.command("模型")
    async def list_models(self, event: AstrMessageEvent):
        """列出可用模型"""
        try:
            yield event.plain_result("🔍 正在获取模型列表...")
            models = await self._get_models_list()
            if models:
                model_list = "\n".join([f"- {m}" for m in models])
                yield event.plain_result(f"✅ 可用模型 ({len(models)}个):\n{model_list}")
            else:
                yield event.plain_result("⚠️ 未获取到模型列表，请确认 RVCSVC-API 服务已启动并加载模型")
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            yield event.plain_result(f"❌ 获取模型列表失败: {e}")

    @filter.command("setmodel")
    @filter.command("设置模型")
    async def set_model(self, event: AstrMessageEvent):
        """设置默认模型"""
        args = event.message_str.split()
        if len(args) < 2:
            yield event.plain_result("用法：/setmodel 模型名")
            return
        model_name = args[1]
        self.default_model = model_name
        yield event.plain_result(f"✅ 已设置默认模型为: {model_name}")

    # ==================== 配置命令 ====================

    @filter.command("setbase")
    @filter.command("设置后端")
    async def set_base_url(self, event: AstrMessageEvent):
        """设置 RVCSVC-API 后端地址"""
        args = event.message_str.split()
        if len(args) < 2:
            yield event.plain_result("用法：/setbase http://127.0.0.1:7860")
            return
        new_url = args[1].rstrip("/")
        self.base_url = new_url
        yield event.plain_result(f"✅ 已设置后端地址为: {new_url}")

    @filter.command("setplat")
    @filter.command("设置平台")
    async def set_plat(self, event: AstrMessageEvent):
        """设置默认搜索平台（qq=QQ音乐，wy=网易云）"""
        args = event.message_str.split()
        if len(args) < 2 or args[1].lower() not in ("qq", "wy"):
            yield event.plain_result("用法：/setplat qq|wy（qq=QQ音乐，wy=网易云音乐）")
            return
        new_plat = args[1].lower()
        self.jkapi_plat = new_plat
        self._default_api = self._create_api(new_plat)
        yield event.plain_result(f"✅ 已设置默认搜索平台为: {new_plat}")

    @filter.command("setadaptive")
    @filter.command("设置自适应变调")
    async def set_adaptive(self, event: AstrMessageEvent):
        """开启/关闭自适应变调（升降调为 0 时自动计算最优变调值）"""
        args = event.message_str.split()
        if len(args) >= 2 and args[1].lower() in ("on", "true", "1", "开", "1"):
            self.adaptive_shift = True
            yield event.plain_result(f"✅ 自适应变调已开启（舒适区 {self.comfort_low}-{self.comfort_high}，升降调为 0 时自动计算）")
        elif len(args) >= 2 and args[1].lower() in ("off", "false", "0", "关", "0"):
            self.adaptive_shift = False
            yield event.plain_result("✅ 自适应变调已关闭（将使用命令/配置中的升降调值）")
        else:
            status = "开启" if self.adaptive_shift else "关闭"
            yield event.plain_result(f"当前自适应变调状态: {status}（舒适区 {self.comfort_low}-{self.comfort_high}）\n用法：/setadaptive on|off")

    @filter.command("setcomfort")
    @filter.command("设置舒适区")
    async def set_comfort(self, event: AstrMessageEvent):
        """设置舒适区音域（自适应变调的参考音域）

        用法：
        /setcomfort A3 A5   # 低音 A3，高音 A5
        也可单独设置：
        /setcomfort A3      # 只改低音
        """
        args = event.message_str.split()
        if len(args) < 2:
            yield event.plain_result(f"当前舒适区: {self.comfort_low}-{self.comfort_high}\n用法：/setcomfort 低音 高音（如 A3 A5）")
            return
        new_low = args[1].upper()
        new_high = args[2].upper() if len(args) > 2 else self.comfort_high
        self.comfort_low = new_low
        self.comfort_high = new_high
        yield event.plain_result(f"✅ 已设置舒适区为: {new_low}-{new_high}")

    @filter.command("health")
    @filter.command("健康检查")
    async def health_check(self, event: AstrMessageEvent):
        """检查后端服务状态"""
        is_healthy = await self._health_check()
        if is_healthy:
            yield event.plain_result("✅ RVCSVC-API 服务正常")
        else:
            yield event.plain_result(f"❌ RVCSVC-API 服务异常，请检查地址: {self.base_url}")

    # ==================== API 实例管理 ====================

    def _create_api(self, plat: str) -> JKApiMusicAPI:
        """懒创建并缓存 jkapi API 实例"""
        plat = (plat or "qq").lower()
        if plat not in ("qq", "wy"):
            plat = "qq"
        if plat not in self._api_cache:
            self._api_cache[plat] = JKApiMusicAPI(api_key=self.jkapi_key, plat=plat)
        return self._api_cache[plat]

    # ==================== 翻唱 API 调用 ====================

    async def _call_convert_api(
        self,
        song_name: str,
        model: str,
        key_shift: int,
        direct_link: str,
        a_capella: bool = False,
        event: Optional["AstrMessageEvent"] = None,
    ) -> Dict[str, Any]:
        """
        调用 RVCSVC-API（app_rvc.py）的 Gradio /convert 端点（直链模式）。

        后端真实逻辑：
        - 第一个参数 = 音频下载直链（mp3/wav/flac 等），后端直接下载并做缓存键
        - model_dropdown = RVC 模型文件名（如 wuxiV1.pth）
        - 返回音频文件路径（后端本地路径，需通过 /gradio_api/file= 下载）
        - a_capella=True 时后端跳过伴奏合成，直接输出纯人声（{歌名}-清唱.mp3）

        参数顺序（13 个，与后端 btn.click 注册一致）：
        [direct_link, key_shift, vocal_vol, inst_vol, model_dropdown,
         reverb_intensity, delay_intensity, uvr_choice, f0_method,
         adaptive_shift, comfort_low, comfort_high, a_capella]

        event：传入当前消息事件时，输出文件写入该会话的 workspaces 目录
        （data/workspaces/_FriendMessage_{QQ号}/rvc_cover 或
        data/workspaces/_GroupMessage_{群号}/rvc_cover）；不传则用固定输出目录。
        """
        out_dir = self._resolve_output_dir(event)
        try:
            payload = {
                "data": [
                    str(direct_link),                 # 音频下载直链（必填）
                    int(key_shift),                   # 升降调
                    self.default_vocal_vol,           # vocal_vol 人声音量 dB
                    self.default_inst_vol,            # inst_vol 伴奏音量 dB
                    model or "wuxiV1.pth",            # model_dropdown RVC 模型
                    self.reverb_intensity,            # 混响强度（0干声~10宏大，默认4）
                    self.delay_intensity,             # 回声(延迟)效果（0关闭~10明显，默认0）
                    self.uvr_choice,                  # 人声分离模型 (UVR)
                    self.f0_method,                   # F0 音高提取算法（rmvpe/pm/fcpe）
                    self.adaptive_shift,              # 是否启用自适应变调（True=升降调为0时自动计算）
                    self.comfort_low,                 # 舒适区低音（音名，如 A3）
                    self.comfort_high,                # 舒适区高音（音名，如 A5）
                    bool(a_capella),                  # 清唱模式（True=不合成伴奏，只出纯人声）
                ]
            }

            api_url = f"{self.base_url}/gradio_api/call/convert"

            # 全程使用同一个 client，避免 SSE 轮询时连接已关闭
            client = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout))
            try:
                resp = await client.post(api_url, json=payload)

                if resp.status_code != 200:
                    return {"success": False, "error": f"提交翻唱任务失败: HTTP {resp.status_code}"}

                event_data = resp.json()
                event_id = event_data.get("event_id")
                if not event_id:
                    return {"success": False, "error": f"后端未返回 event_id: {event_data}"}

                # 轮询等待任务完成。Gradio call 协议是 SSE 长连接（content-type: text/event-stream），
                # GET /gradio_api/call/{api_name}/{event_id} 会阻塞直到 job 结束并推送 'event: complete' + 'data: [...]'。
                # 任务已完成时会立即返回。用 stream + 分段 timeout 处理，避免死锁。
                # 每 15s 打一次心跳日志，让运行者（LLM/控制台）看到"仍在处理中"而非假死。
                poll_url = f"{self.base_url}/gradio_api/call/convert/{event_id}"
                deadline = time.time() + self.request_timeout
                text = ""
                got_final = False
                last_heartbeat = time.time()
                while time.time() < deadline:
                    try:
                        async with client.stream("GET", poll_url, timeout=httpx.Timeout(120, connect=10)) as r:
                            text_parts = []
                            async for chunk in r.aiter_text():
                                text_parts.append(chunk)
                                full = "".join(text_parts)
                                now = time.time()
                                # 心跳：每 15s 记一条"仍在处理"日志（不刷屏，间隔固定）
                                if now - last_heartbeat >= 15:
                                    elapsed = int(now - (deadline - self.request_timeout))
                                    logger.info(
                                        f"[RVC-Cover] 翻唱进行中... 已等待 {elapsed}s / 上限 {self.request_timeout}s"
                                        f"（后端 7860 控制台输出\"已导出\"表示完成）"
                                    )
                                    last_heartbeat = now
                                # SSE 末尾是 'event: complete' + 'data: [{"path":..., "url":...}]'
                                if "event: complete" in full:
                                    got_final = True
                                    break
                            text = "".join(text_parts)
                    except httpx.TimeoutException:
                        # 流 120s 未结束：任务仍在跑，继续等
                        now = time.time()
                        if now - last_heartbeat >= 15:
                            elapsed = int(now - (deadline - self.request_timeout))
                            logger.info(f"[RVC-Cover] 翻唱仍在处理中... 已等待 {elapsed}s")
                            last_heartbeat = now
                        continue
                    if got_final:
                        break

                if not got_final:
                    return {"success": False, "error": f"翻唱超时 ({self.request_timeout}秒)"}

                # 解析 SSE data 行（JSON 是文件 dict 列表，非 status 包装）
                data_lines = [l[6:] for l in text.splitlines() if l.startswith("data: ")]
                if not data_lines:
                    return {"success": False, "error": "未能从后端获取结果"}
                result_data = json.loads(data_lines[-1])

                # 提取输出文件路径/URL
                out = None
                if isinstance(result_data, list) and result_data:
                    item = result_data[0]
                    if isinstance(item, dict):
                        out = item.get("path") or item.get("url")
                    elif isinstance(item, str):
                        out = item
                elif isinstance(result_data, dict):
                    out = result_data.get("name") or result_data.get("path") or result_data.get("url")
                elif isinstance(result_data, str):
                    out = result_data

                if not out:
                    return {"success": False, "error": "翻唱完成但未能获取输出文件"}

                # 优先用后端提供的 url（绝对 URL），否则用 path 拼 file= 端点
                if out.startswith("http"):
                    file_url = out
                else:
                    import urllib.parse
                    encoded = urllib.parse.quote(out, safe="")
                    file_url = f"{self.base_url}/gradio_api/file={encoded}"

                try:
                    fresp = await client.get(file_url, follow_redirects=True)
                    if fresp.status_code == 200 and len(fresp.content) > 0:
                        safe_name = self._sanitize_filename(Path(out).name or "cover_output.mp3")
                        local_path = out_dir / safe_name
                        local_path.write_bytes(fresp.content)
                        logger.info(f"翻唱结果已下载: {local_path} ({len(fresp.content)} bytes)")

                        # QQ 语音 1 分钟上限 —— 把长音频按"句子边界"切成 50-59s 的小段
                        # 清唱模式先剪掉段内大段间奏静音（保留换气/小停顿），再切段
                        parts = self._split_for_qq_voice(
                            local_path, song_name,
                            trim_silence=bool(a_capella), out_dir=out_dir,
                        )
                        if parts:
                            part_list = "\n".join(
                                [f"  第{i+1}段: {p}" for i, p in enumerate(parts)]
                            )
                            return {
                                "success": True,
                                "output_path": str(local_path),
                                "parts": parts,
                                "message": (
                                    f"翻唱完成: 《{song_name}》\n"
                                    f"完整文件: {local_path}\n"
                                    f"已按 {len(parts)} 段切分（QQ语音 1 分钟上限），请**按顺序**发送：\n"
                                    f"{part_list}"
                                ),
                            }
                        return {
                            "success": True,
                            "output_path": str(local_path),
                            "parts": [str(local_path)],
                            "message": (
                                f"翻唱完成: 《{song_name}》\n"
                                f"输出文件: {local_path}（时长未超 1 分钟，无需切分）"
                            ),
                        }
                    return {"success": False, "error": f"下载翻唱结果失败: HTTP {fresp.status_code}"}
                except Exception as dl_err:
                    logger.error(f"下载翻唱结果失败: {dl_err}")
                    return {"success": False, "error": f"下载翻唱结果失败: {dl_err}"}
            finally:
                await client.aclose()

        except httpx.TimeoutException:
            return {"success": False, "error": f"请求超时 ({self.request_timeout}秒)"}
        except Exception as e:
            logger.error(f"API 调用失败: {traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    # ==================== ffmpeg PATH 探测 ====================

    @staticmethod
    def _ensure_ffmpeg_on_path() -> None:
        """确保 pydub 能找到 ffmpeg（AstrBot GUI 启动时 PATH 可能缺失 WinGet 安装的 ffmpeg）"""
        import shutil

        if shutil.which("ffmpeg"):
            return  # 已在 PATH

        # ffmpeg 候选：
        # 1. 当前 python 解释器目录及上级（RVCSVC-API\runtime 自带 ffmpeg.exe）
        # 2. 插件目录的上级 RVCSVC-API\runtime\ffmpeg.exe（常见同级布局）
        # 3. WinGet 包目录（Windows 常用安装方式）
        pydir = os.path.dirname(sys.executable)
        candidates = [
            os.path.join(pydir, "ffmpeg.exe"),
            os.path.normpath(os.path.join(pydir, "..", "ffmpeg.exe")),
            os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "RVCSVC-API", "runtime", "ffmpeg.exe")
            ),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\Microsoft\WinGet\Packages"),
        ]
        extra_dirs: List[str] = []
        for c in candidates:
            if os.path.isdir(c):
                extra_dirs.append(c)
                # WinGet 目录是通配符包目录，需展开找 ffmpeg-* 子目录
                if c.endswith("Packages"):
                    try:
                        for sub in os.listdir(c):
                            if sub.startswith("Gyan.FFmpeg") or "ffmpeg" in sub.lower():
                                full = os.path.join(c, sub)
                                # 找 bin/ffmpeg.exe
                                for root, _, files in os.walk(full):
                                    if "ffmpeg.exe" in files:
                                        extra_dirs.append(root)
                                        break
                    except Exception:
                        pass
            elif os.path.isfile(c):
                # 直接命中 ffmpeg.exe 文件，追加它所在目录
                extra_dirs.append(os.path.dirname(c))

        # 也把 python 解释器同目录 + 常见位置兜底
        extra_dirs.append(os.path.dirname(sys.executable))

        merged = os.pathsep.join(extra_dirs) + os.pathsep + os.environ.get("PATH", "")
        os.environ["PATH"] = merged
        # pydub 的 ffmpeg 探测在 import pydub 时已缓存，这里清掉缓存
        import pydub
        pydub.utils.ffmpeg_location = None

        if not shutil.which("ffmpeg"):
            logger.warning(
                f"[_ensure_ffmpeg_on_path] 未在 PATH 找到 ffmpeg（已追加 {len(extra_dirs)} 个候选目录）。"
                "pydub 切分将回退为整文件不切分，建议安装 ffmpeg (winget install Gyan.FFmpeg)"
            )
        else:
            logger.info(f"[_ensure_ffmpeg_on_path] ffmpeg 已可用: {shutil.which('ffmpeg')}")

    # ==================== QQ 语音切分（句子边界感知） ====================

    def _trim_long_silence(self, audio: "AudioSegment",
                            silence_dbfs: float = -42.0,
                            min_silence_ms: int = 1500,
                            keep_gap_ms: int = 1000,
                            min_voice_ms: int = 1200) -> "AudioSegment":
        """剪掉长段静默（间奏），保留短停顿（换气/小节奏停顿）。

        判定：连续 RMS < silence_dbfs 的区间，若长度 ≥ min_silence_ms 视为"大段间奏"，
        剪掉但保留 keep_gap_ms（默认 1000ms）的自然间隙，让句间停顿舒缓不急促。
        若剪后某句语音 < min_voice_ms（太短的碎片），与前后句合并，不单独丢弃。
        """
        from pydub import AudioSegment

        step_ms = 50
        total_ms = len(audio)
        # 按 50ms 窗口算 RMS dBFS
        rms_db: List[tuple] = []
        for start in range(0, total_ms - step_ms + 1, step_ms):
            seg = audio[start:start + step_ms]
            rms_db.append((start, seg.dBFS))

        # 找出所有"静默"窗口（RMS 低于阈值）
        silent = {s for (s, db) in rms_db if db < silence_dbfs}

        # 合并成连续静默区间 [start_ms, end_ms)
        intervals: List[List[int]] = []
        for s, _ in rms_db:
            if s not in silent:
                continue
            if intervals and s - intervals[-1][1] <= step_ms:
                intervals[-1][1] = s + step_ms
            else:
                intervals.append([s, s + step_ms])

        # 只处理长度 ≥ min_silence_ms 的大段静默；保留 keep_gap_ms 间隙
        keep = []  # 要保留的语音区间 [start_ms, end_ms)
        cursor = 0
        for (s0, s1) in intervals:
            if (s1 - s0) < min_silence_ms:
                continue  # 短停顿（换气/节奏停顿），整段保留
            # 剪掉前保留 keep_gap_ms 的自然间隙（让句子不粘死）
            gap_end = s0 + keep_gap_ms
            if gap_end > cursor:
                keep.append([cursor, gap_end])
            cursor = s1  # 跳过大段静默
        if cursor < total_ms:
            keep.append([cursor, total_ms])

        # 合并相邻的语音片段，丢弃 < min_voice_ms 的碎片
        merged: List[List[int]] = []
        for (a0, a1) in keep:
            if a1 - a0 < min_voice_ms:
                continue
            if merged and a0 - merged[-1][1] <= step_ms * 2:
                merged[-1][1] = a1
            else:
                merged.append([a0, a1])

        # 重新拼音频
        out = AudioSegment.silent(duration=0)
        for (a0, a1) in merged:
            out += audio[a0:a1]

        logger.info(
            f"[_trim_long_silence] 剪掉 {sum(s1-s0 for s0,s1 in intervals if s1-s0>=min_silence_ms)/1000:.1f}s "
            f"大段静默（{sum(1 for s0,s1 in intervals if s1-s0>=min_silence_ms)} 段 ≥{min_silence_ms}ms），"
            f"原 {total_ms/1000:.1f}s → 现 {len(out)/1000:.1f}s"
        )
        return out

    def _split_for_qq_voice(self, audio_path: Path, song_name: str = "",
                            trim_silence: bool = False,
                            out_dir: Optional[Path] = None) -> List[str]:
        """把长音频按"句子边界"切成 50-59s 的小段，供 QQ 语音（1分钟上限）发送。

        核心原则：切分点必须落在**句子间静音低谷**，不能切进完整的一句。
        算法：
        0. 若 trim_silence=True（清唱），先剪掉段内大段间奏静音（保留换气/小停顿）
        1. pydub 读入，按 1s 窗口算 RMS dB
        2. 目标段长 50-59s（取中值 54.5s）
        3. 在 [48s, 65s] 搜索窗口内找**最安静**的 1s 位置作为切分点
           （最安静 = RMS 最低，即歌词/乐句间的自然停顿）
        4. 若找不到足够安静的位置（整段都在唱，无明显停顿），
           则取 [55s] 强制切（宁可切到句中，不破坏 1 分钟上限）
        5. 逐段导出为 mp3（192k），顺序命名 part1, part2, ...
        """
        try:
            # 确保 pydub 能调用 ffmpeg：AstrBot 从 GUI 启动时 PATH 中往往
            # 没有 WinGet/系统安装的 ffmpeg 目录，这里主动探测并注入
            self._ensure_ffmpeg_on_path()

            from pydub import AudioSegment
            import pydub  # noqa: F401  # 确保导入

            audio = AudioSegment.from_file(str(audio_path))

            # 清唱模式：先剪掉大段间奏静音，保留换气/小停顿
            if trim_silence:
                audio = self._trim_long_silence(audio, keep_gap_ms=self.trim_keep_gap_ms)

            total_ms = len(audio)
            logger.info(f"[_split_for_qq_voice] 源音频 {total_ms/1000:.1f}s，开始切分"
                         + ("（清唱：已剪大段间奏静音）" if trim_silence else ""))

            # 输出基目录：未指定 out_dir 时用默认输出目录
            base_out = out_dir if out_dir is not None else self.output_dir

            # 如果总时长 <= 59.5s，无需切分
            if total_ms <= 59500:
                # 清唱且总时长变短了，把剪静音后的音频导出为单段
                if trim_silence:
                    out = base_out / f"{song_name or 'cover'}_trimmed.mp3"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    audio.export(str(out), format="mp3", bitrate="192k")
                    logger.info(f"[_split_for_qq_voice] 剪后 {total_ms/1000:.1f}s <=59.5s，导出单段 {out}")
                    return [str(out)]
                return [str(audio_path)]

            # 计算 1s 窗口 RMS 序列（用于找静音低谷）
            step_ms = 1000
            rms_vals: List[tuple] = []  # (start_ms, rms_db)
            for start in range(0, total_ms - step_ms, step_ms):
                seg = audio[start:start + step_ms]
                if len(seg) < step_ms // 2:
                    break
                rms_vals.append((start, seg.dBFS))

            parts: List[AudioSegment] = []
            cursor_ms = 0
            idx = 0

            while cursor_ms < total_ms:
                remaining = total_ms - cursor_ms
                if remaining <= 59500:
                    # 最后一段（<=59.5s），整块拿走，保证任何一段都不超 QQ 1 分钟上限
                    parts.append(audio[cursor_ms:])
                    break

                idx += 1
                # 目标段长 54.5s；搜索窗口 [48s, 65s]
                target_end = cursor_ms + 54500
                search_lo = cursor_ms + 48000
                search_hi = min(cursor_ms + 65000, total_ms - 5000)
                if search_hi <= search_lo:
                    search_hi = search_lo + 1000

                # 在 [search_lo, search_hi] 内找 RMS 最低（最安静）的 1s 位置
                best_cut = -1
                best_rms = float("inf")
                for (s, db) in rms_vals:
                    if s < search_lo or s > search_hi:
                        continue
                    if db < best_rms:
                        best_rms = db
                        best_cut = s + step_ms  # 切在静音结束后

                # 60s 硬上限保护：QQ 语音 1 分钟，任何一段不得超 59.5s
                if best_cut - cursor_ms > 59500:
                    best_cut = cursor_ms + 59500
                    logger.info(f"[_split_for_qq_voice] 段{idx}: 切分点超 60s，已压缩到 59.5s")

                if best_cut <= 0:
                    # 没找到合适静音，取 54.5s 强制切
                    best_cut = cursor_ms + 54500
                    logger.info(f"[_split_for_qq_voice] 段{idx}: 未找到静音，强制切 {54.5}s")
                else:
                    logger.info(
                        f"[_split_for_qq_voice] 段{idx}: 在 {best_cut-cursor_ms/1000:.0f}s 处"
                        f"切分（RMS {best_rms:.1f}dBFS）"
                    )

                parts.append(audio[cursor_ms:best_cut])
                cursor_ms = best_cut

            # 导出为 mp3
            # 丢弃尾巴：若最后一段 < 8s（太短的碎片），直接不要了
            if len(parts) > 1 and len(parts[-1]) < 8000:
                logger.info(f"[_split_for_qq_voice] 末尾碎片 {len(parts[-1])/1000:.1f}s < 8s，已丢弃")
                parts.pop()

            stem = song_name or "cover"
            parts_dir = base_out / f"{stem}_parts"
            parts_dir.mkdir(parents=True, exist_ok=True)
            # 清理旧的 part 文件
            for old in parts_dir.glob("*.mp3"):
                old.unlink(missing_ok=True)

            result_paths: List[str] = []
            for i, seg in enumerate(parts):
                p = parts_dir / f"part{i+1}.mp3"
                seg.export(str(p), format="mp3", bitrate="192k")
                result_paths.append(str(p))
                logger.info(f"[_split_for_qq_voice] 第{i+1}段 {len(seg)/1000:.1f}s -> {p}")

            logger.info(f"[_split_for_qq_voice] 共 {len(result_paths)} 段，目录 {parts_dir}")
            return result_paths

        except Exception as e:
            logger.error(f"[_split_for_qq_voice] 切分失败，回退为单文件: {traceback.format_exc()}")
            # 回退：整文件发送（可能超 1 分钟，但保证不丢）
            return [str(audio_path)]

    # ==================== 输出目录 ====================

    def _get_session_output_dir(self, event: "AstrMessageEvent") -> Path:
        """获取"当前会话"的输出目录。

        - 私信 → data/workspaces/_FriendMessage_{QQ号}/rvc_cover
        - 群聊 → data/workspaces/_GroupMessage_{群号}/rvc_cover

        基目录 = AstrBot 的 data/workspaces（通过 astrbot.core.utils 动态获取，
        桌面版/开发版路径不同）。若获取不到（非 AstrBot 环境，如单元测试），
        回退到 self.output_dir。
        """
        if self._output_dir_fixed:
            return self.output_dir
        umo = getattr(event, "unified_msg_origin", "") or ""
        if not umo:
            return self.output_dir
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path
            from astrbot.core.workspace import normalize_umo_for_workspace
            # 用 AstrBot 官方函数，保证目录名与 AstrBot 实际创建的完全一致
            ws_root = Path(get_astrbot_workspaces_path())
            safe = normalize_umo_for_workspace(umo)
        except Exception:
            # 非 AstrBot 环境（单元测试等）：回退到插件数据目录下的 workspaces 子目录
            # （self.output_dir 此时是 data/plugin_data/astrbot_plugin_rvc_cover/）
            safe = self._normalize_umo_fallback(umo)
            ws_root = self.output_dir / "workspaces"
        out = ws_root / safe / "rvc_cover"
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception:
            return self.output_dir
        logger.info(f"[RVC-Cover] 会话输出目录: {out}")
        return out

    @staticmethod
    def _normalize_umo_fallback(umo: str) -> str:
        """非 AstrBot 环境下的会话目录名规范化（与官方 normalize_umo_for_workspace 一致）。"""
        import re as _re
        return _re.sub(r"[^A-Za-z0-9._-]+", "_", umo.strip()) or "unknown"

    def _sanitize_filename(self, name: str) -> str:
        """净化文件名：去掉 Windows/命令行下危险的字符（& ? = / \\ : * " < > | 空格）。

        后端导出的文件名可能包含未编码的直链片段（如 guid=xxx&vkey=yyy），
        其中的 & 在 Windows cmd 里是命令分隔符，会导致 CreateFile 返回
        ERROR_FILE_NOT_FOUND（Errno 2）。全部替换成 _ 即可。
        """
        import re
        # 保留扩展名前的部分，把非法字符统一换成下划线
        safe = re.sub(r'[&?:*"<>/\\|=]', '_', name)
        # 多个下划线压平
        safe = re.sub(r'_+', '_', safe).strip('_') or "cover_output.mp3"
        # 防止文件名过长（Windows 单文件路径上限 260，留余量）
        return safe[:150]

    def _resolve_output_dir(self, event: Optional["AstrMessageEvent"]) -> Path:
        """返回输出目录：有 event 时按会话，无 event 时用固定/回退目录。"""
        if event is not None:
            return self._get_session_output_dir(event)
        return self.output_dir

    # ==================== 其他辅助方法 ====================

    def _parse_cover_args(self, message: str) -> Dict[str, Any]:
        """解析翻唱指令参数：/cover 歌名 [-p qq|wy] [-m 模型] [-k 升降调] /linkcover 直链 ..."""
        args = {"song": ""}
        # 先去掉已知的命令前缀
        cleaned = message
        for kw in ["/cover", "/翻唱", "/linkcover", "/直链翻唱"]:
            cleaned = cleaned.replace(kw, " ")
        parts = cleaned.split()

        i = 0
        while i < len(parts):
            if parts[i] in ["-m", "--model"]:
                i += 1
                if i < len(parts):
                    args["model"] = parts[i]
            elif parts[i] in ["-k", "--key"]:
                i += 1
                if i < len(parts):
                    try:
                        args["key_shift"] = int(parts[i])
                    except ValueError:
                        pass
            elif parts[i] in ["-p", "--plat", "--platform"]:
                i += 1
                if i < len(parts):
                    args["platform"] = parts[i].lower()
            elif parts[i] not in ["-m", "--model", "-k", "--key", "-p", "--plat", "--platform"]:
                # 歌名/直链：以空白分词，连续的非选项词拼接为完整歌名
                if not args["song"]:
                    args["song"] = parts[i]
                else:
                    # 多词歌名拼接（如 /cover 晴天 周杰伦）
                    args["song"] += " " + parts[i]
            i += 1

        return args

    async def _get_models_list(self) -> List[str]:
        """获取可用 RVC 模型列表（调用 7897 /run/infer_refresh）"""
        try:
            # 模型列表由 RVC 推理服务（7897）直接提供，不经过 7860 的 Gradio
            import re as _re
            # 从 rvc_base_url（7860）推断同机的 7897 地址
            host = "127.0.0.1"
            m = _re.match(r"https?://([^/]+)", self.base_url)
            if m:
                host = m.group(1)
            rvc_url = f"http://{host}:7897"
            resp = httpx.post(f"{rvc_url}/run/infer_refresh", json={"data": []}, timeout=10)
            if resp.status_code == 200:
                d = resp.json()
                data = d.get("data", [])
                if data and isinstance(data[0], dict):
                    return data[0].get("choices", [])
                if data and isinstance(data[0], list):
                    return [str(m) for m in data[0]]
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
        return []

    async def _health_check(self) -> bool:
        """检查后端服务状态"""
        try:
            api_url = f"{self.base_url}/gradio_api/info"
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(api_url)
                return resp.status_code == 200
        except Exception:
            return False

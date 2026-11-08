"""
音乐搜索 API 适配器
- jkapi.com 第三方接口（唯一音源）
  GET https://jkapi.com/api/music?name=歌名&plat=qq|wy&type=json&apiKey=xxx
  返回单条结果：{"code": 1, "name": ..., "artist": ..., "album": ..., "music_url": "..."}

依赖 httpx（AstrBot 自带），不引入 aiohttp，保证插件在任何 AstrBot 环境都能加载。
"""

import json
import re
from typing import Any, Dict, List, Optional

import httpx
from astrbot.api import logger


class JKApiMusicAPI:
    """jkapi.com 音乐接口（QQ 音乐 / 网易云音乐）

    plat 取值：
    - "qq"：QQ 音乐（默认）
    - "wy"：网易云音乐

    TLS 策略：默认使用标准证书校验（verify=True）。仅当请求抛出具体的
    SSL/TLS 握手错误（常见于 Windows 部分环境系统证书链缺失），且用户
    允许自动降级（tls_verify_autofallback，默认 True）时，才重建为
    verify=False 的 client 重试一次。降级会发出警告日志；若用户显式
    禁用该选项，则直接放弃重试并记录错误。
    """

    name = "jkapi"
    BASE_URL = "https://jkapi.com/api/music"
    VALID_PLATS = ("qq", "wy")

    def __init__(
        self,
        api_key: str = "",
        plat: str = "qq",
        request_timeout: float = 30.0,
        tls_verify_autofallback: bool = True,
    ):
        plat = (plat or "qq").lower()
        if plat not in self.VALID_PLATS:
            logger.warning(f"无效 plat: {plat}，回退到 qq")
            plat = "qq"
        self.api_key = api_key
        self.plat = plat
        self._request_timeout = float(request_timeout)  # 由 main.py 注入 search_timeout
        # 是否允许 TLS 校验失败时自动降级为 verify=False（默认允许）
        self._tls_verify_autofallback = bool(tls_verify_autofallback)
        self._client: Optional[httpx.AsyncClient] = None
        # 当前 client 使用的 TLS 校验状态：True=校验证书（默认），False=已降级为不校验
        self._tls_verify: Any = True

    @staticmethod
    def _is_tls_error(e: Exception) -> bool:
        """判断异常是否属于 SSL/TLS 握手/证书错误。"""
        if isinstance(e, (httpx.ConnectError, httpx.RemoteProtocolError)):
            return True
        msg = str(e).lower()
        return any(
            kw in msg
            for kw in (
                "certificate",
                "ssl",
                "handshake",
                "tls",
                "credential",
                "凭证",
                "secure channel",
            )
        )

    def _make_client(self, verify: Any) -> httpx.AsyncClient:
        """构造一个新的 AsyncClient（内层 timeout 比外层多 5s 缓冲）。"""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._request_timeout + 5),
            verify=verify,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            verify = True if self._tls_verify is True else False
            self._client = self._make_client(verify)
        return self._client

    def _downgrade_to_unverified(self) -> bool:
        """降级到 verify=False 并返回新 client；不允许降级时返回 None。"""
        if not self._tls_verify_autofallback:
            logger.error(
                "jkapi TLS 证书校验失败，且未允许自动降级"
                "（tls_verify_autofallback=False），放弃重试。"
            )
            return None
        logger.warning(
            "jkapi TLS 证书校验失败，降级为 verify=False 重试一次。"
            "apiKey 将经未校验的 HTTPS 连接发送，请确认为可信网络环境；"
            "如需保持证书校验，请修复本地 CA 链或设置 tls_verify_autofallback=False。"
        )
        self._tls_verify = False
        if self._client is not None and not self._client.is_closed:
            self._client = None
        return self._get_client()

    def _build_params(self, keyword: str) -> dict:
        """jkapi 需要 4 个参数：name, plat, type, apiKey（httpx 自动做 URL 编码）

        注意：jkapi 的 name 参数用**下划线**分隔词组（空格分隔会直接搜不到），
        例如 "We Don't Talk Anymore" 必须传 "We_Dont_Talk_Anymore"，
        歌手+歌名组合如 "晴天 周杰伦" 传 "晴天_周杰伦"。这里自动把空格转成下划线。
        """
        name = re.sub(r"\s+", "_", keyword.strip())
        return {
            "name": name,
            "plat": self.plat,
            "type": "json",
            "apiKey": self.api_key,
        }

    async def _do_search(
        self, keyword: str, client: httpx.AsyncClient, params: dict, inner_timeout: float
    ) -> List[Dict[str, str]]:
        """单次 GET 请求并解析 jkapi 响应。"""
        resp = await client.get(self.BASE_URL, params=params, timeout=inner_timeout)
        if resp.status_code != 200:
            logger.error(f"jkapi 搜索失败: HTTP {resp.status_code}")
            return []
        # jkapi 的 Content-Type 是 text/json，直接按文本解析
        data = json.loads(resp.text)
        if data.get("code") != 1:
            logger.warning(f"jkapi 返回 code={data.get('code')}: {data.get('msg')}")
            return []
        return [
            {
                "id": keyword,
                "name": data.get("name", keyword),
                "artists": data.get("artist", "未知"),
                "album": data.get("album", ""),
                "url": data.get("music_url", ""),
                "platform": "jkapi-" + self.plat,
            }
        ]

    async def fetch_data(self, keyword: str, limit: int = 5) -> List[Dict[str, str]]:
        """按歌名搜索，返回含 music_url（直链）的单条结果。

        jkapi 每次只返回最匹配的一首歌。
        code==1 表示成功；music_url 即音频下载直链（可直接喂给 RVCSVC-API）。
        """
        if not self.api_key:
            logger.warning("未配置 jkapi apiKey，无法搜索")
            return []

        client = self._get_client()
        params = self._build_params(keyword)
        # 内层 timeout = search_timeout + 5s 缓冲，避免外层 asyncio.wait_for(search_timeout)
        # 比内层先取消导致 TimeoutError（之前内层 20s > 外层 15s 配反了）
        inner_timeout = self._request_timeout + 5
        try:
            return await self._do_search(keyword, client, params, inner_timeout)
        except Exception as e:
            # TLS/证书类错误（如 Windows 部分环境系统证书链缺 jkapi.com 证书）：
            # 若用户允许，降级 verify=False 重试一次；否则直接失败并记录日志。
            if self._is_tls_error(e):
                downgraded = self._downgrade_to_unverified()
                if downgraded is not None:
                    try:
                        return await self._do_search(keyword, downgraded, params, inner_timeout)
                    except Exception as e2:
                        logger.error(f"jkapi TLS 降级后仍失败: {e2}")
                else:
                    logger.error(
                        f"jkapi 搜索异常（TLS 证书校验失败且未允许自动降级）: {e}"
                    )
            else:
                logger.error(f"jkapi 搜索异常: {e}")
            return []

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


__all__ = ["JKApiMusicAPI"]

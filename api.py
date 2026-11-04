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
    """

    name = "jkapi"
    BASE_URL = "https://jkapi.com/api/music"
    VALID_PLATS = ("qq", "wy")

    def __init__(self, api_key: str = "", plat: str = "qq", request_timeout: float = 30.0):
        plat = (plat or "qq").lower()
        if plat not in self.VALID_PLATS:
            logger.warning(f"无效 plat: {plat}，回退到 qq")
            plat = "qq"
        self.api_key = api_key
        self.plat = plat
        self._request_timeout = float(request_timeout)  # 由 main.py 注入 search_timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._tls_verify: Any = True  # TLS 握手失败后自动降级为 False

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Windows 部分环境系统证书链缺 jkapi.com 证书，TLS 握手报
            # "安全包中没有可用的凭证"。首次即用 verify=False 兜底
            #（API key 鉴权 + 响应 code 校验仍生效；第三方聚合接口场景可接受）。
            # 内层 client 超时比外层 asyncio.wait_for 多 5s 缓冲，避免内外配反
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._request_timeout + 5),
                verify=False,
            )
            self._tls_verify = False
        return self._client

    def _retry_tls_unverified(self):
        """TLS 握手失败时：关闭旧 client，重建（verify=False 已默认，此函数保留兼容）。"""
        self._tls_verify = False
        if self._client is not None and not self._client.is_closed:
            self._client = None

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
        except Exception as e:
            # Windows 部分环境系统证书链缺 jkapi.com 证书 → TLS 握手报
            # "安全包中没有可用的凭证"。降级 verify=False 重试一次。
            msg = str(e)
            if ("凭证" in msg or "certificate" in msg.lower()
                    or "ssl" in msg.lower() or "handshake" in msg.lower()):
                self._retry_tls_unverified()
                try:
                    client = self._get_client()
                    resp = await client.get(self.BASE_URL, params=params, timeout=inner_timeout)
                    if resp.status_code == 200:
                        data = json.loads(resp.text)
                        if data.get("code") == 1:
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
                except Exception as e2:
                    logger.error(f"jkapi 降级后仍失败: {e2}")
            logger.error(f"jkapi 搜索异常: {e}")
            return []

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


__all__ = ["JKApiMusicAPI"]

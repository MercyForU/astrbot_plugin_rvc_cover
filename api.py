"""
音乐搜索 API 适配器
- jkapi.com 第三方接口（唯一音源）
  GET https://jkapi.com/api/music?name=歌名&plat=qq|wy&type=json&apiKey=xxx
  返回单条结果：{"code": 1, "name": ..., "artist": ..., "album": ..., "music_url": "..."}

依赖 httpx（AstrBot 自带），不引入 aiohttp，保证插件在任何 AstrBot 环境都能加载。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class JKApiMusicAPI:
    """jkapi.com 音乐接口（QQ 音乐 / 网易云音乐）

    plat 取值：
    - "qq"：QQ 音乐（默认）
    - "wy"：网易云音乐
    """

    name = "jkapi"
    BASE_URL = "https://jkapi.com/api/music"
    VALID_PLATS = ("qq", "wy")

    def __init__(self, api_key: str = "", plat: str = "qq"):
        plat = (plat or "qq").lower()
        if plat not in self.VALID_PLATS:
            logger.warning(f"无效 plat: {plat}，回退到 qq")
            plat = "qq"
        self.api_key = api_key
        self.plat = plat
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30))
        return self._client

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
        try:
            resp = await client.get(self.BASE_URL, params=params, timeout=20)
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
            logger.error(f"jkapi 搜索异常: {e}")
            return []

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


__all__ = ["JKApiMusicAPI"]

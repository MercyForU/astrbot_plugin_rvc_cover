# astrbot_plugin_rvc_cover 🎤

基于 ✨[RVCSVC-API-Pro](https://github.com/MercyForU/RVCSVC-API-Pro)✨的 AstrBot AI 翻唱插件。

**前置依赖**：

**手动**部署：

  获取[RVCSVC-API-Pro](https://github.com/MercyForU/RVCSVC-API-Pro) 并且部署适合自己显卡型号的[RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) 附上 [国内获取RVC-Project](https://www.yuque.com/flowercry/hxf0ds)
  **注意**：需要运行**RVC模型**中`go-webui.bat`再启动**RVCSVC-API-Pro**中`启动 rvcapi.bat`本插件方可正常使用。

或者**许愿式**部署：

  将下面这段提示词发送给任意可调用工具的Agent（如果需要指定路径请自行添加提示词）：
  
```
请根据https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI帮我部署适合我显卡的RVC模型；
再在https://github.com/MercyForU/RVCSVC-API-Pro项目的README中选择合适的链接获取RVCSVC-API-Pro整合包并解压；
全部部署完成后我先需要运行RVC模型中的：go-webui.bat等待go-webui.bat完全启动后（端口7897被监听），再启动RVCSVC-API-Pro中的：启动 rvcapi.bat；
请帮我写成一键启动脚本放在桌面上命名为：启动AI翻唱助手后端.bat
```

**工作流程**：

用户说"翻唱/清唱 某首歌"

  → LLM 调用 search_music（jkapi.com 搜出音频直链 music_url）
  → LLM 调用 rvc_cover / a_capella_cover（带直链走后端 /convert）
  → RVCSVC-API 下载 → UVR5/MSST 分离 → RVC/SVC 变声 → 音效后处理 → 混音/清唱导出
  → 插件下载 MP3 → 按句子切分（规避语音 1 分钟上限）→ 逐段发送

本插件音源**入口** [jkapi](https://jkapi.com)

# 🙏🏻**特别感谢**✨[jkapi](https://jkapi.com)✨先给这样的公益大佬磕一个 **orz**
# 🙏🏻**特别感谢**✨[RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)✨让小鱼开口**唱歌**成为可能

---

## 目录

- [前置依赖](#-前置依赖)
- [安装](#-安装)
- [配置](#-配置)
- [命令](#-命令)
- [LLM 工具](#-llm-工具)
- [自适应变调](#-自适应变调)
- [清唱模式](#-清唱模式)
- [语音发送说明](#-语音发送说明)
- [故障排查](#-故障排查)

---

## 🔗 前置依赖

1. **RVCSVC-API-Pro**（RVCSVC-API魔改版）已启动并加载模型[RVCSVC-API-Pro获取](https://github.com/MercyForU/RVCSVC-API-Pro)
   - 默认 `http://127.0.0.1:7860`
   - 后端只接受音频下载直链（mp3/wav/flac/m4a 等）
   - 详见 `RVCSVC-API-Pro/README.md`
2. **jkapi.com 密钥**（注册 https://jkapi.com 获取，填到 `jkapi_api_key`）
3. **AstrBot** v4.16+
4. **ffmpeg** 在 PATH 中（pydub 切分用；Windows 可 `winget install Gyan.FFmpeg`）

---

## 📦 安装

把插件目录放入 AstrBot 插件目录：

   ```
   data/plugins/astrbot_plugin_rvc_cover/
   ```


---

## ⚙️ 配置

在 `data/plugins_config/astrbot_plugin_rvc_cover/.json` 配置（也可在 dashboard 插件配置页编辑）：

| 配置键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `jkapi_api_key` | string | `""` | jkapi.com 密钥（**必填**，翻唱音源唯一入口） |
| `jkapi_plat` | string | `"qq"` | 默认搜索平台：`qq`=QQ音乐 / `wy`=网易云 |
| `rvc_base_url` | string | `"http://127.0.0.1:7860"` | RVCSVC-API 后端地址 |
| `default_model` | string | `""` | 默认翻唱模型（如 `wuxiV1.pth`），留空用后端默认 |
| `reverb_intensity` | int | `4` | 混响强度 0~10（0 干声 / 4 默认 / 10 宏大） |
| `delay_intensity` | int | `0` | 回声（延迟）效果 0~10（0 关闭） |
| `default_key_shift` | int | `0` | 未指定 `-k` 时的默认升降调（−12~12） |
| `default_vocal_vol` | int | `0` | 人声音量默认值（dB，−3~3） |
| `default_inst_vol` | int | `0` | 伴奏音量默认值（dB，−3~3） |
| `uvr_choice` | string | `"VR-HP5 (默认)"` | 人声分离模型，枚举见下 |
| `f0_method` | string | `"rmvpe"` | F0 音高提取算法，枚举见下 |
| `adaptive_shift` | bool | `true` | 是否启用自适应变调（升降调为 0 时自动算最优值） |
| `comfort_low` | string | `"A3"` | 自适应变调舒适区低音（音名） |
| `comfort_high` | string | `"A5"` | 自适应变调舒适区高音（音名） |
| `trim_keep_gap_ms` | int | `1000` | 清唱静音修剪保留的自然间隙（ms），越大节奏越舒缓（0~5000，只影响清唱） |
| `search_timeout` | int | `15` | jkapi 搜索超时（秒） |
| `request_timeout` | int | `300` | 翻唱请求超时（秒） |
| `llm_tool_timeout` | int | `300` | LLM 工具超时（秒），应 ≥ `request_timeout` |
| `output_dir` | string | `"output"` | 填绝对路径则全局固定该目录；不填时：会话调用（`/cover`、LLM 翻唱）按发起会话写入 `data/workspaces/`（私信→`_FriendMessage_{QQ号}/rvc_cover`，群聊→`_GroupMessage_{群号}/rvc_cover`），非会话调用（测试/CLI）写入插件数据目录 `data/plugin_data/astrbot_plugin_rvc_cover/` |

### `uvr_choice` 接受的精确值（字符串全等匹配）

| 写入值（原样照抄） | 实际走哪个分离器 | 说明 |
|---|---|---|
| `VR-HP5 (默认)` | UVR-HP5 | 默认，速度快 |
| `MSST BS-Roformer` | MSST-BS-RoFormer | 高精度单模型 |
| `MSST 串联管线 (becruily_deux + 去混响)` | MSST 串联 | 两步提干声 + 去混响 |

> 后端用 `uvr_choice in [...]` 做**全等判断**，传错一个字符（空格/全半角/大小写）会被静默回退到 VR-HP5，不报错。

### `f0_method` 接受的精确值

| 写入值 | 说明 |
|---|---|
| `rmvpe` | 默认，精度最高 |
| `pm` | 旧版 |
| `fcpe` | 全卷积，适合无 RMVPE 的轻量模型 |

---

## 📜 命令

| 命令 | 说明 |
|---|---|
| `/cover 歌名 [-p qq\|wy] [-m 模型] [-k 升降调]` | 搜直链并翻唱（人声+伴奏混音） |
| `/linkcover 直链URL [-m 模型] [-k 升降调]` | 用直链直接翻唱（跳过搜索） |
| `/capella 歌名 [-p qq\|wy] [-m 模型] [-k 升降调]` | **清唱**（只出纯人声，不合成伴奏） |
| `/linkcapella 直链URL [-m 模型] [-k 升降调]` | 用直链直接清唱 |
| `/coverhelp` | 显示帮助 + 当前生效配置 |
| `/model` | 列出可用模型 |
| `/setmodel 模型名` | 设置默认模型（运行时） |
| `/setplat qq\|wy` | 设置默认搜索平台（运行时） |
| `/setadaptive on\|off` | 开关自适应变调（运行时） |
| `/setcomfort 低音 高音` | 设置舒适区音域（如 `/setcomfort A3 A5`） |
| `/setbase 地址` | 设置后端地址（运行时） |
| `/health` | 后端健康检查 |

示例：

```
/cover 晴天
/cover 晴天 -m 塔菲 -k 12
/cover 晴天 -p wy
/capella 晴天 -m 塔菲      # 清唱，只要人声
```

---

## 🤖 LLM 工具

LLM 模式下用户直接说"翻唱 XXX" / "清唱 XXX" 即可，插件注册的工具：

| 工具名 | 说明 |
|---|---|
| `search_music(keyword, platform?)` | 按歌名搜索（返回最匹配结果 + 直链） |
| `rvc_cover(song_name, artist_name?, model_name?, key_shift?, music_source?, a_capella?)` | 搜直链并翻唱；`a_capella=True` 时走清唱通道 |
| `a_capella_cover(song_name, artist_name?, model_name?, key_shift?, music_source?)` | **清唱专用**：用户说"清唱/无伴奏/只要人声"时 LLM 调用此工具，后端跳过伴奏合成，直接输出纯人声 |
| `smart_cover(song_name?, model_name?, key_shift?)` | 智能翻唱（自动从上下文补全歌名） |

LLM 行为约定：
- 用户说「翻唱」→ `rvc_cover`（`a_capella=False`）
- 用户说「清唱 / 无伴奏 / 只要人声 / 纯人声」→ `a_capella_cover` 或 `rvc_cover(a_capella=True)`
- 指定歌手版本时务必传 `artist_name`

---

## 🎯 自适应变调

当 `adaptive_shift=true`（默认）且**手动升降调为 0**（未指定 `-k` 或 `-k 0`）时，
后端会用分离出的纯人声 + 舒适区音域（`comfort_low`~`comfort_high`，默认 A3~A5）
自动计算最优升降调（`±12` 钳制）。手动指定非 0 的 `-k` 则跳过自适应。

- 默认开启；可用 `/setadaptive off` 临时关闭
- 舒适区可用 `/setcomfort A3 A5` 调整
- 依赖后端 `RVCSVC-API/` 根目录的 `adaptive_pitch.py`（F0 提取**优先走 RMVPE GPU 快路径**：需 `rmvpe.pt` + `infer/rmvpe.py`，缺任一或无 GPU 时自动回退 `librosa.pyin` CPU，不阻断主流程）

---

## 🎤 清唱模式

`/capella`、`/linkcapella` 或 LLM 说"清唱"走清唱通道：
后端在推理完人声、做完 EQ/压缩/混响/延迟/响度归一化后，**跳过伴奏加载、伴奏变调、混音**，
直接输出 `temp/{歌名}-清唱.mp3`（纯人声，保留所有音效）。

正常翻唱通道输出 `temp/{歌名}-RVC-AI翻唱.mp3`（人声+伴奏混音）。

### 清唱静音修剪

清唱结果在切 QQ 语音段之前，会先剪掉**大段间奏静音**（≥1500ms 的连续静默），
保留**短停顿**（换气/小节奏停顿 <1500ms）。剪掉的大段间奏保留 `trim_keep_gap_ms`（默认 1000ms）
的自然间隙，避免句子粘连。节奏快慢可用配置项 `trim_keep_gap_ms`（0~5000ms）调整：
- 值越大 → 句间停顿越长，节奏越舒缓
- 值越小 → 越紧凑

正常翻唱通道**不裁剪**静音，只按句子停顿切段。

---

## 📂 输出目录（按会话归档）

**会话调用**（`/cover`、`/capella`、LLM 翻唱等带 `event` 的入口）按**发起会话**归档到 AstrBot 的 `data/workspaces/` 目录：

| 会话类型 | 输出路径 |
|---|---|
| 私信 | `data/workspaces/_FriendMessage_{QQ号}/rvc_cover/` |
| 群聊 | `data/workspaces/_GroupMessage_{群号}/rvc_cover/` |

完整目录名由会话的 `unified_msg_origin`（格式 `平台:类型:会话ID`，如 `aiocqhttp:FriendMessage:2934160465`）
规范化得到（非字母数字下划线点减号 → `_`），例如 `aiocqhttp:FriendMessage:2934160465` → `_FriendMessage_2934160465`。

**非会话调用**（单元测试、CLI 等拿不到 `event` 的入口）固定写入 AstrBot 插件数据目录
`data/plugin_data/astrbot_plugin_rvc_cover/`（由 `StarTools.get_data_dir` 创建，可迁移、可审计，不污染插件安装目录）。

若 `output_dir` 配置了**绝对路径**（如 `D:/covers`），则忽略上述全部规则，所有输出固定写入该目录。

> 插件**不在插件安装目录内写任何文件**（音频/临时数据一律写到 AstrBot 数据目录），保证迁移与审计友好。

---

## 📱 语音发送说明

QQ 语音消息有 **1 分钟上限**。长音频会自动按**句子停顿处**切成 50~59 秒小段
（`{歌名}_parts/partN.mp3`，位于上述输出目录中），并**按顺序**逐段发送（每段间隔 2 秒），不会切进完整的一句。

LLM 工具返回结果会列出所有分段路径，模型据此按顺序发语音。

---

## 🛠️ 故障排查

### 插件加载失败 / 「插件不存在」

- 确认 AstrBot 实例环境装了 `httpx`（插件已不依赖 aiohttp）
- 改过 schema 后重启 AstrBot
- 看 AstrBot 启动日志里 `Failed to load plugin` 的具体 traceback

### 搜索无结果

- 确认 `jkapi_api_key` 已配置
- 换平台（`-p qq` / `-p wy` 或 `/setplat`）
- 检查网络 / API 风控（jkapi 有频率限制）
- jkapi 的 `name` 参数用**下划线**分隔词组（插件内部已自动把空格转下划线，无需手动）

### ffmpeg 找不到（pydub 切分回退为整文件）

- 插件会按以下顺序自动探测 ffmpeg（不依赖固定绝对路径）：
  1. 当前 python 解释器目录及上级（RVCSVC-API\runtime 自带 ffmpeg.exe）
  2. 插件目录上级的 `RVCSVC-API\runtime\ffmpeg.exe`（常见同级布局）
  3. `%USERPROFILE%\AppData\Local\Microsoft\WinGet\Packages`（WinGet 安装的 ffmpeg）
- 若仍找不到，装一次即可：`winget install Gyan.FFmpeg`

### 翻唱失败

- 确认 RVCSVC-API 已启动（`/health`）
- 确认模型已加载（`/model` 查看列表）
- 直链需可匿名访问（VIP 歌曲加密直链通常不行）
- 改过后端参数（F0/自适应/清唱）需重启 RVCSVC-API

### 后端报「Value is not in the list of choices」

- `uvr_choice` / `f0_method` 传值与后端枚举不一致
- 确认 schema 的 `enum` 与后端 `gr.Radio`/`gr.Dropdown` 的 `choices` 逐字符一致
- 复跑 `tests/verify_schema_vs_backend.py` 比对

### 清唱没跳过伴奏 / 自适应没生效

- 确认后端 `app_rvc.py` 已更新并**重启**（新增参数需重启识别）
- 清唱需第 13 位参数 `a_capella=True`
- 自适应需 `adaptive_shift=True` 且 `key_shift==0`，且后端 `RVCSVC-API/` 根目录有 `adaptive_pitch.py`
  （要 GPU 加速还需 `rmvpe.pt` + `infer/rmvpe.py`，缺任一会回退 CPU 的 pyin，不影响出结果）

---

## ⚖️ 许可证

仅供学习、研究和个人娱乐使用。请勿用于商业用途或生成侵害他人权益的内容；使用者对生成内容自行承担法律责任。

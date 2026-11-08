# astrbot_plugin_rvc_cover 🎤

## 基于 ✨[RVCSVC-API-Pro](https://github.com/MercyForU/RVCSVC-API-Pro)✨的 AstrBot AI 翻唱插件。

# **前置依赖**：

## 🤟**手动**部署（请**不要使用**EDGE浏览器获取，大文件在EDGE浏览器可能会**卡99.9%**）：

  
### **一键轻松部署大力气版超级整合包**（rvc_cover_backend_X）：

  依据你的**显卡型号**选择合适的版本：[N卡50系（国内镜像）](https://hf-mirror.com/MercyStar/RVCSVC-API-Pro/resolve/main/rvc_cover_backend_50.7z?download=true)[10.9GB]； [N卡非50系（国内镜像）](https://hf-mirror.com/MercyStar/RVCSVC-API-Pro/resolve/main/rvc_cover_backend_N.7z?download=true)[11.0GB]； [A卡或核显（国内镜像）](https://hf-mirror.com/MercyStar/RVCSVC-API-Pro/resolve/main/rvc_cover_backend_A.7z?download=true)[8.82GB]；

---
  

## 🌠或者**许愿式**部署：

  将下面这段提示词发送给任意可调用工具的Agent（如果需要指定路径请自行添加提示词）：
  
```
请根据帮我下载（路径不要选择在C盘）适合我显卡的整合包：[N卡50系国内镜像](https://hf-mirror.com/MercyStar/RVCSVC-API-Pro/resolve/main/rvc_cover_backend_50.7z?download=true)10.9GB [N卡非50系国内镜像](https://hf-mirror.com/MercyStar/RVCSVC-API-Pro/resolve/main/rvc_cover_backend_N.7z?download=true)11.0GB  [A卡系列国内镜像](https://hf-mirror.com/MercyStar/RVCSVC-API-Pro/resolve/main/rvc_cover_backend_A.7z?download=true)8.82GB
下载完成后帮我解压，并为其中的：一键启动.bat 创建桌面快捷方式。
```


## ⏯️**启动依赖**：

  将**一键轻松部署大力气版超级整合包**解压后运行`一键启动.bat`待其拉起两个服务，本插件方可正常使用。
  使用**补丁包**与**RVCSVC-API-Pro-V2.0**版本的需要运行RVC-Project模型中`go-webui.bat`待其完全启动后，再启动RVCSVC-API-Pro中`start_rvcapi.bat`本插件方可正常使用。



## 🔗**相关链接**：

 **密钥**获取：✨[jkapi官网](https://jkapi.com)✨
 
 RVC-Project仓库：✨[RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)✨

 RVCSVC-API-Pro仓库：✨[RVCSVC-API-Pro](https://github.com/MercyForU/RVCSVC-API-Pro)✨

 RVCSVC-API-Pro的huggingface仓库：✨[RVCSVC-API-Pro](https://huggingface.co/MercyStar/RVCSVC-API-Pro/tree/main)✨



# 🙏🏻**特别感谢**✨[jkapi](https://jkapi.com)✨先给这样的公益大佬磕一个 **orz**
# 🙏🏻**特别感谢**✨[RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)✨让小鱼开口**唱歌**成为可能


## 🔗 前置依赖

1. **RVCSVC-API-Pro**（RVCSVC-API魔改版）已启动并加载模型[RVCSVC-API-Pro仓库](https://github.com/MercyForU/RVCSVC-API-Pro)
   - 默认 `http://127.0.0.1:7860`
   - 后端只接受音频下载直链（mp3/wav/flac/m4a 等）
   - 详见 `RVCSVC-API-Pro/README.md`
2. **jkapi.com 密钥**（注册 https://jkapi.com 获取，填到 `jkapi_api_key`）
3. **AstrBot** v4.16+
4. **ffmpeg** 在 PATH 中（pydub 切分用；Windows 可 `winget install Gyan.FFmpeg`）

---

## 📦 安装

克隆本项目放入 AstrBot 插件目录：

   ```
   data/plugins/astrbot_plugin_rvc_cover/
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

## 🎼 【新增实验性功能】分声部变调（男女合唱 / 双声部）

男女合唱歌用整轨自适应变调时，**男声部分正常了、女声部分会被推到超出模型音域的位置，出现电音撕裂**
（同一首里两个声部各需要不同升降调）。开启 `split_duet=true` 后，后端做**男/女声部提纯双轨独立推理**：

**处理流程**（全自动，不需要你给切分点）：
1. UVR5 抽出的混合人声 → 后端 7860 调 **MSST BS-Roformer 男女分离模型**（`Chorus_Male_Female_BS_Roformer`，SDR≈24.13）提纯出**男轨 + 女轨**两条单声部轨
2. 男轨单独跑 RMVPE 提 F0 → 按舒适区算**男声最优升降调** → 整轨 RVC 推理（各自 key_shift）
3. 女轨单独跑 RMVPE 提 F0 → 按舒适区算**女声最优升降调** → 整轨 RVC 推理（各自 key_shift）
4. 两轨按时间轴叠加合成（防削波）→ 得到"男段男调、女段女调"的整轨人声 → 音效/伴奏

**模型说明：**
- 提纯模型是 `Sucial/Chorus_Male_Female_BS_Roformer`（BS-Roformer 架构，复用 7860 现成 MSST 框架，零新依赖），**不是** `melband-roformer-infer` 那个 Mel-Band 架构——该库只封装 Mel-Band、无法加载 BS-Roformer 权重，故弃用
- 模型训练自 750 首中文歌曲（opencpop + m4singer），**实验性**：对中文合唱效果好，非中文歌曲提纯可能退化
- 模型按**性别特征**分轨（基频/共振峰），不是按时间切段：
  - **同时段合唱**（男女叠唱）→ 拆得最干净 ✅
  - **交替唱**（一段男一段女不重叠）→ 男轨填男段、女轨填女段，静音补另一轨，叠加后等价"交替变调" ✅

**配置：** `split_duet`（默认 `false`），在 dashboard 插件配置页切换，**重启 AstrBot 生效**。
**后端依赖：** 需 7860 部署了 `mf_separate.py` 模块 + `msst/pretrain/male_female_models/` 下的男女分离模型权重；缺失时自动回退，不报错。

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

## ⚖️ 许可证

仅供学习、研究和个人娱乐使用。请勿用于商业用途或生成侵害他人权益的内容；使用者对生成内容自行承担法律责任。

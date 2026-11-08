# 更新日志

## v2.1.3 (2026-11-08)

### 🐛 修复
- **修复 async 翻唱轮询循环中的同步阻塞**（`main.py`）：SSE 重连退避处的 `time.sleep(2)` 改为 `await asyncio.sleep(2)`。原实现在 async 上下文中直接调用 `time.sleep` 会阻塞整个 AstrBot 事件循环（期间所有并发消息/任务都被卡住 2s），现改为让出事件循环的异步等待

### 🔒 安全加固
- **jkapi TLS 证书校验策略重构**（`api.py`）：
  - 原实现无条件 `verify=False`，apiKey 直接经未校验 TLS 连接明文提交，存在中间人窃取密钥风险
  - 现默认 `verify=True`（标准证书校验），仅当 SSL/TLS 握手/证书类错误发生时（如 Windows 部分环境系统证书链缺失 jkapi.com 证书）才自动降级为 `verify=False` 重试一次，并打印明确警告日志
  - 新增 `tls_verify_autofallback` 参数（默认 `True`）：设为 `False` 时 TLS 校验失败直接报错放弃，绝不降级，适合对密钥安全有更高要求的部署
  - 整理出 `_is_tls_error` / `_make_client` / `_downgrade_to_unverified` / `_do_search` 等方法，消除原先两段重复的降级重试内联代码

## v2.1.0 (2026-11-06)

### ✨ 新功能
- **LLM 工具内强制接管发送**（`force_plugin_send`，默认关闭）
  - 开启后，LLM 调用 `rvc_cover` / `a_capella_cover` / `smart_cover` 工具翻唱完成时，**插件在工具内部直接向用户发送语音**，不再依赖 LLM 调用 `send_message_to_user`
  - 发送走 QQ 语音消息组件（`MessageChain + Record`），多级回退：`MessageChain + Record` → `event.record / record_result` → `event.image`，避免把语音发成附件
  - 多段之间保持 2 秒间隔，规避 QQ 只发第一条的问题
  - 工具返回值改为强提示「已发送，别再发」，阻止 LLM 重复发送/发成文件
  - 作用范围：仅 LLM 对话工具（`rvc_cover` / `a_capella_cover` / `smart_cover`）；`/cover`、`/capella`、`/linkcover` 等指令不受影响，仍走原发送流程
  - 关闭时行为与 v2.0.0 完全一致，不影响老用户

### 🐛 修复
- 修复 `_llm_a_capella_cover` **重复搜索**问题：旧实现先搜一次做校验，再让 `_llm_rvc_cover` 又搜一次（一次清唱多打一次 jkapi 请求，且两次结果可能不同）；现简化为直接复用 `_llm_rvc_cover(a_capella=True)`
- 修复 `/coverhelp` 帮助文本中 JSON 示例的代码围栏导致 `main.py` 三引号未闭合、插件 import 报 `SyntaxError: unterminated triple-quoted string literal` 的问题
- 修复 `/coverhelp` 文本中 `{你的QQ号}`、`{歌名}` 等未转义花括号在 `.format()` 时抛 `KeyError` 的问题
- 修复 `/coverhelp` 文本中 `{har_model_note}` 占位符未传参导致的 `.format()` 报错
- 修复 `_split_for_qq_voice` 切分日志里 `best_cut-cursor_ms/1000` 运算符优先级错误（实际按 `best_cut - (cursor_ms/1000)` 计算），现正确输出段内切分位置
- 修复 `/setadaptive` 参数列表中 `"1"` 重复出现的问题

### 🔧 内部优化
- `_conf_schema.json` → `output_rules` 新增 `force_plugin_send` 配置项（bool，默认 `false`）
- 启动日志新增 `force_plugin_send=开启/关闭` 状态输出，便于确认配置是否加载
- `/coverhelp` 中各状态字符串改为预计算变量，避免 `.format()` 内嵌长条件表达式
- 新增 `_send_voice_parts_via_event()` 方法：在 async 工具函数内用 `await event.send(...)` 主动发送语音（原 `_send_cover_result` 为生成器，仅适用于指令流）

---

## v2.0.0 (2024-11-04)

### 🎯 核心功能新增
- **伴唱/和声功能**：新增 `split_duet` 分声部变调模式，支持男/女声部分离并各自变调推理
  - 新增 `harmony_model` 伴唱音色配置
  - 新增 `harmony_comfort_low` / `harmony_comfort_high` 伴唱舒适区配置
  - 新增 `harmony_vol` 伴唱音量配置 (-20~+10 dB)
- **远程访问模式** (`remote_access`)
  - 开启后优先使用后端返回的 `url` 字段直接下载，适合远程服务部署
  - 关闭时兼容本地路径转下载 URL 兼容模式
- **RVC 模型自动发现**（规划中，待实现动态获取后端模型列表）

### 🎨 界面/体验优化
- **配置分组重构**：按功能重组为 6 大类，每组添加 emoji 标识
  - 🔧 后端配置
  - 🎤 主唱配置
  - 🔍 音乐搜索
  - 📤 输出规则
  - 🎛 效果器
  - 🧪 实验性功能
- **语音发送说明重写**：新增 LLM 必读工具调用提醒
  - 必须使用 `send_message_to_user` 发送语音
  - 参数格式：`{"messages": [{"type": "record", "path": "音频文件路径"}]}`
  - 分段发送间隔 2 秒，避免 QQ 语音发送失败
- **auto_split 默认关闭**：默认不再自动切分，避免长音频被意外切分
- **配置项精简**：移除 `f0_method`（固定使用 `rmvpe`），减少用户决策成本
- **远程访问模式置顶**：配置项移至最前，优先决策本地/远程模式
- **配置分组重构**：按功能重组为 6 大类，每组添加 emoji 标识
  - 🔧 后端配置
  - 🎤 主唱配置
  - 🔍 音乐搜索
  - 📤 输出规则
  - 🎛 效果器
  - 🧪 实验性功能
- **语音发送说明重写**：新增 LLM 必读工具调用提醒
  - 必须使用 `send_message_to_user` 发送语音
  - 参数格式：`{"messages": [{"type": "record", "path": "音频文件路径"}]}`
  - 分段发送间隔 2 秒，避免 QQ 语音发送失败
- **auto_split 默认关闭**：默认不再自动切分，避免长音频被意外切分
- **配置项精简**：移除 `f0_method`（固定使用 `rmvpe`），减少用户决策成本
- **远程访问模式置顶**：配置项移至最前，优先决策本地/远程模式



---

## v1.1.4 (2026-11-01)

- 新增 `auto_split` 配置项（默认开启）：长音频自动切 50-59s 段发送；关闭后不切分、整段直发（>60s 可能被 QQ 语音拒收/截断，用户自担）
- 切分规则调整：末尾短尾巴（<8s）不再丢弃，照常作为最后一段发送
- 修复：清唱 + `auto_split` 关闭时，发送环节误发原始文件、剪过间奏的 `_trimmed.mp3` 被忽略的 bug（现单段发送优先发 `parts[0]`）
- 剪间奏（清唱静音修剪）增强：
  - 新增 `trim_silence_dbfs` 配置（默认 -36dBFS，原写死 -42）——RVC 清唱间奏常带底噪/混响尾巴，阈值可调更敏感
  - 新增 `trim_min_silence_ms` 配置（默认 1500ms）——只剪连续静默 ≥ 此值的段，保护换气/节奏停顿
  - 修复合并语音片段时时间戳丢失的 bug（原逻辑会误删 1s 短真句）
  - 增加诊断日志（剪掉时长 / 保留段数 / 剪后时长）

## v1.1.3 (2026-10-31)

- 作为正式版发布
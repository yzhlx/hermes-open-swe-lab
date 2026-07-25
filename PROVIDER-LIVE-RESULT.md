# PROVIDER-LIVE-RESULT.md — 中转 OpenAI 兼容接口 × 固定 Open SWE 基线 P1–P6 验证

**工作线：** 并行线路 B — `provider-live`（独立于 D3 / 部署系统）
**基线：** `phase-1-smoke` @ `9d1d6356b039f0cc29278781486ddfb2831df8e4`（= `origin/phase-1-smoke` 当前 tip）
**固定上游：** `langchain-ai/open-swe` @ `ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6`
**目标配置：** `OPEN_SWE_OPENAI_BASE_URL` / `OPEN_SWE_OPENAI_API_KEY` / `OPEN_SWE_OPENAI_MODEL` / `OPEN_SWE_OPENAI_USE_RESPONSES=false` / `OPEN_SWE_DISABLE_CROSS_PROVIDER_FALLBACK=true` / `LANGSMITH_GATEWAY_ENABLED=false`
**LangSmith：** 已永久移除，本线路不 reintroduce（adapter 在 `LANGSMITH_GATEWAY_ENABLED=true` 时显式 `RelayConfigError`）。

---

## 1. 凭证只读检查（不输出任何 Key / 完整 .env）

对**本机**做了只读检查（未触碰服务器受限配置、未打印任何值、未写入 Git/日志/Issue/PR）：

| 位置 | 结果 |
| --- | --- |
| Shell 环境变量 `OPEN_SWE_OPENAI_BASE_URL` / `_API_KEY` / `_MODEL` | 全部 `absent` |
| 本机 `.env` / `.env.local` | 不存在 |
| `.env.example` | 仅含变量**名**（占位符，非真实凭证） |
| 服务器 `/opt/hermes-open-swe-lab/.env` | 本 Windows 主机不可达（预期；由用户在云控制面验证） |
| Git 跟踪的密钥文件（`.env`/`.pem`/`.key`/`secrets/`） | 无 |

**结论：本机未找到中转 API 凭证。** 缺失的变量名（live 运行所需）：
`OPEN_SWE_OPENAI_BASE_URL`、`OPEN_SWE_OPENAI_API_KEY`、`OPEN_SWE_OPENAI_MODEL`。
（其中 `_API_KEY` 为唯一密钥；`_BASE_URL` / `_MODEL` 为配置项。）所有不依赖真实凭证的代码、Mock 测试、测试脚本均已完成，不阻塞其他开发线。

---

## 2. 固定 Open SWE 基线模型调用路径审计（只读，未 clone/修改上游）

通过 `gh api` 读取上游 `agent/utils/model.py` 与 `agent/completion.py`（commit `ed12bb8d`），关键证据：

| 行 | 事实 | 影响 |
| --- | --- | --- |
| `model.py:10` | `OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"` | **官方 WebSocket Responses 硬编码路径** |
| `model.py:125-129` | `openai:` 模型默认 `base_url=wss…`、`use_responses_api=True` | 上游**默认走 Responses over WebSocket** |
| `model.py:92-99` | `_coerce_openai_chat_completions_kwargs`：`use_responses_api is not False` 时直接返回；为 `False` 时转 `reasoning_effort` | Chat Completions（HTTPS）仅在 `use_responses_api=False` 时启用 |
| `model.py:102-111` | `_configure_openai_responses_kwargs`：`use_responses_api is False` 时直接返回（不附加 Responses-only 参数） | 同上 |
| `model.py:14` | `DEFAULT_MAX_RETRIES = 6` | 上游自带重试 |
| `model.py:158-169` | `fallback_model_id_for`：Anthropic↔OpenAI 跨 Provider 回退 | 需由本 adapter **关闭** |
| `model.py:117-119` | `use_gateway` 解析 `LANGSMITH_GATEWAY_ENABLED` | 本线路恒为 `false` |

**结论：**
- 上游默认强制 Responses-over-WebSocket；要改用中转 Chat Completions，必须使 `use_responses_api=False` 且 `base_url` 指向 HTTPS 中转端点。
- 本 adapter 的 `OPEN_SWE_OPENAI_USE_RESPONSES=false` 正是该信号的来源；而本 adapter 自身**始终**以配置的 HTTPS `base_url` 构造 OpenAI client（从不构造 `wss://`），并在 `config.load_relay_config` 中**拒绝 `ws://`/`wss://` base_url**，从而在集成边界上彻底禁用官方 WebSocket Responses 硬编码路径。
- 上游不读取字面量 `OPEN_SWE_OPENAI_*`（代码搜索 `total: 0`）。这些变量是 lab 控制面侧配置；运行时由控制面翻译为 Open SWE 的 `make_model(model_id="openai:<MODEL>", base_url=<HTTPS relay>, api_key=<relay key>, use_responses_api=False)`。该翻译属于受保护的 `hermes_worker` 控制面，本线路**不修改**（见第 8 节范围边界）。

---

## 3. 本线路对 relay adapter 的实现/修复（9 项行为）

文件：`hermes_open_swe_relay/{config,client,streaming}.py`（均在既有 relay adapter 目录内）。

| # | 行为 | 实现 | 位置 |
| --- | --- | --- | --- |
| 1 | 非流式响应 | `chat_completion()` → `chat.completions.create`（默认路径） | `client.py` |
| 2 | 流式响应 | `chat_completion_stream()` 返回 chunk 迭代器 + `StreamAccumulator` 重建消息 | `client.py` / `streaming.py` |
| 3 | tool_calls | 透传 `tools`/`tool_choice`；`extract_tool_calls()` 归一化 `id/type/function.name/arguments` | `client.py` |
| 4 | finish_reason | `extract_finish_reason()` 从 `choices[0].finish_reason` 提取 | `client.py` |
| 5 | usage 字段 | `extract_usage()` 提取 `prompt/completion/total_tokens` | `client.py` |
| 6 | 超时 | client 级默认 `timeout=120s`（`DEFAULT_TIMEOUT`），支持 per-call 覆盖；传入 OpenAI client | `client.py` |
| 7 | 429 与 5xx 重试 | `_with_retry` 对 429 / 5xx / timeout 重试（默认 3 次，指数退避，**尊重 `Retry-After`**）；SDK `max_retries` 强制为 0，重试策略单一可追溯 | `client.py` |
| 8 | 无效响应处理 | `validate_response()`：空 `choices` / 既无 `choices` 也无 `id` / `None` → `RelayInvalidResponseError` | `client.py` |
| 9 | secret 脱敏 | `redact()` / `redact_headers()` 剥离 `Authorization`、key、`api_key` 等；重试耗尽与校验错误信息均经 `redact()` | `redact.py` / `client.py` |

新增结构化错误（均带 `code`）：`RelayError` / `RelayRequestError` / `RelayTimeoutError` / `RelayRetryExhaustedError` / `RelayInvalidResponseError`。
新增边界防护：`load_relay_config` 拒绝 `ws://`/`wss://` base_url（禁用 WebSocket Responses 路径）。

---

## 4. P1–P6 逐项结果（离线，Mock 中转，确定性）

所有项均以 `FakeRelay` 模拟 OpenAI 兼容端点验证，无需网络或 `openai` SDK。

| ID | 检查 | 结果 | 证据（测试） |
| --- | --- | --- | --- |
| **P1** | Endpoint 与鉴权 | **PASS** | `test_p1_*`：`build_relay_client({})`→`None`（opt-in）；`wss://` base_url 被拒；`api_key` 正确传入 client；`LANGSMITH_GATEWAY_ENABLED=true` 被拒 |
| **P2** | 指定模型可调用 | **PASS** | `test_p2_model_forwarded_and_echoed`：model 透传并在响应中回显 |
| **P3** | 最小非流式对话 | **PASS** | `test_p3_nonstream_content_finish_reason_usage`：content + `finish_reason=stop` + usage 齐全 |
| **P4** | 流式增量输出 | **PASS** | `test_p4_stream_yields_incremental_deltas` / `test_p4_accumulator_reconstructs_message`：增量 delta 重建为完整内容，`finish_reason`/`usage` 可提取 |
| **P5** | 工具调用结构兼容 | **PASS** | `test_p5_nonstream_tool_calls_structure` / `test_p5_stream_tool_calls_accumulate`：非流式与流式下 `id/type/function.name/function.arguments` 均正确（流式按 index 累积） |
| **P6** | 固定 Open SWE Agent 最小无仓库任务 | **PASS（集成契约级）** | `test_p6_minimal_agent_no_repo_tool_loop`：模拟 Open SWE Chat Completions 工具循环（`use_responses_api=False` 等价路径），本地执行工具、回灌结果、得到终答；**未 clone/修改 GitHub 仓库、未创建 PR、未使用 Docker、未访问受保护系统** |

> **P6 真机运行（对真实中转端点跑 Open SWE agent）** 为 `NOT_TESTED`：需真实凭证 + 控制面翻译（`OPEN_SWE_OPENAI_*` → Open SWE `make_model(...)`），属用户阻塞（见第 7 节）。集成契约已由 Mock 完整验证。

---

## 5. 模型与 Endpoint 兼容性

- Endpoint：OpenAI 兼容 **Chat Completions over HTTPS**（中转 `base_url`）。与 `OPEN_SWE_OPENAI_USE_RESPONSES=false` 一致，且被上游 `use_responses_api=False` 路径消费。
- 模型：`OPEN_SWE_OPENAI_MODEL` 透传为 `chat.completions.create(model=...)`。
- 兼容性结论：**兼容**（离线 Mock + 上游代码路径双重证据）。真机兼容需凭证后由 `scripts/provider_preflight.py` 复测。

---

## 6. 流式与 tool_calls 结果

- 流式：支持增量 delta 输出与 `StreamAccumulator` 重建；`finish_reason` 与 `usage` 均可从流末 chunk 提取。**PASS**。
- tool_calls：非流式直接返回结构化 `tool_calls`；流式按 `index` 累积 `id/type/function.name/arguments`，重建后与 Open SWE 期望结构一致。**PASS**。

---

## 7. 跨 Provider 回退是否确定关闭

**确定关闭。** 证据：
- `RelayClient.cross_provider_fallback_enabled` 恒为 `False`；`enable_cross_provider_fallback()` 显式 `raise RelayConfigError`。
- `OPEN_SWE_DISABLE_CROSS_PROVIDER_FALLBACK=true` 被读入 `RelayConfig.disable_cross_provider_fallback` 并保留（默认即为关闭）。
- adapter 从不构造任何 Anthropic fallback 配置。
- 测试 `test_cross_provider_fallback_definitively_closed` 覆盖上述三点。

---

## 8. 测试数量与结果

- 运行：`pytest tests/`（离线，无网络 / 无 `openai` SDK 依赖）。
- 结果：**58 passed**（含既有 D1/D2 离线套件，无回归）。
- 本线路直接相关：
  - 既有 relay 离线测试 **17**（未改动契约）：`tests/test_adapter.py` ×12、`tests/test_redact.py` ×5。
  - 新增 `tests/test_provider_live.py` ×**23**（P1–P6 + 9 项行为）。
  - 新增 `hermes_open_swe_relay/streaming.py`（流式累积器）。

---

## 9. 独立 Reviewer 结论

见提交后的更新（本线路运行独立 Reviewer，仅修 blocking 问题，不扩大 scope 至 D3/部署）。

---

## 10. Commit / Draft PR

- Commit SHA：见提交后更新。
- Draft PR：`base=phase-1-smoke`，不合并。链接见提交后更新。

---

## 11. 是否存在唯一用户阻塞

**是，唯一阻塞 = 真实中转凭证 + 控制面翻译（用于 P6 真机运行）。**
- 本地未找到 `OPEN_SWE_OPENAI_BASE_URL` / `OPEN_SWE_OPENAI_API_KEY` / `OPEN_SWE_OPENAI_MODEL`。
- 真机 Live P1–P6 与 P6 Open SWE agent 运行需将上述变量写入云控制面 `/opt/hermes-open-swe-lab/.env`，并由控制面将其翻译为 Open SWE 运行时配置（`use_responses_api=False` + HTTPS base_url + relay key + model）。
- 其余所有代码、Mock 测试、测试脚本、文档均已就绪，不阻塞其他开发线。

---

## 12. 范围边界（本线路未触碰）

按任务约束，本线路仅修改：`hermes_open_swe_relay/`（config/client/streaming）、`tests/test_provider_live.py`、`PROVIDER-LIVE-RESULT.md`。**未修改**：`hermes_worker/control_plane.py`、`worker_api_server.py`、D3 Webhook/状态机、`deploy/`、systemd/反向代理、中央 README、共享架构文档。未 clone/修改 `langchain-ai/open-swe` 上游，未访问 `yzhlx/hermes-learning-os` 或云 Hermes 运行时。

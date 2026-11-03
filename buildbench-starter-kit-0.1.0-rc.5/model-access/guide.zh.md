# Build-Bench 模型接入规范

版本：v0.1 · 2026 年 10 月 15 日

本指南说明如何让 Agent 在托管运行环境中调用组织方提供的模型。平台会在 Agent 启动前提供模型地址、模型别名和任务凭证，请按下述配置接入，无需在提交包中放入个人 API Key。

本地 Starter Kit 用于开发、检查和打包，不提供官方模型访问凭证或开发 API 额度。托管模型接入通过组织方开放的 Hosted Smoke Test 进行测试。

本指南介绍 Chat Completions 接入方法和示例。**模型规格、当前支持的请求参数与功能、功能开放状态及各阶段限额，请以官网最新的[模型接入规范](https://matrix.cstcloud.cn/build-bench/submission.html?lang=zh#model-access)为准。** 使用可选模型功能前，请先查阅该规范；包内示例不代表完整支持范围。

## 1. 读取平台配置

Agent 在隔离沙箱中运行，不能直接访问外网；请使用平台提供的 OpenAI 兼容模型地址。模型配置在 Agent 入口启动前准备好，因此可以在入口中初始化模型客户端。

| 环境变量 | 含义 |
| --- | --- |
| `BB_MODEL_BASE_URL` | 包含 `/v1` 的 API 基地址；传给 `OpenAI(base_url=...)`，不要额外追加 `/chat/completions` |
| `BB_MODEL_NAME` | 组织方统一模型别名；原样传给 `model`，不要写死真实供应商模型名称 |
| `BB_MODEL_TOKEN_FILE` | 本次 Run/Case 的只读短期凭证文件路径；读取内容传给 SDK 的 `api_key` |

请直接读取这三个配置值，不要写死地址、端口、模型名称或凭证路径。所有队伍使用组织方统一配置的模型；任务凭证由平台提供，无需申请或提交供应商 API Key。

任务凭证仅在对应运行期间有效。请勿将凭证写入 ZIP、日志或报错信息。`GET /v1/models` 可检查凭证和模型别名；要确认模型能正常生成内容，还需要完成一次 Chat Completions 请求。

## 2. 请求格式与最新支持范围

生成请求：`POST /v1/chat/completions`。Python SDK：`client.chat.completions.create(...)`。请使用 Chat Completions 的消息及参数格式。

### 查阅官网最新规范

官网[模型接入规范](https://matrix.cstcloud.cn/build-bench/submission.html?lang=zh#model-access)统一维护以下信息：

- 模型规格、单次输出上限及各评测阶段的调用限制。
- 工具调用、流式响应和严格 JSON Schema 等可选功能的当前支持状态与参数写法。
- 推理强度、采样参数和并行工具调用等额外选项的可用范围。

请按官网当前公布的范围适配 Agent，不要仅凭 SDK 提供某个参数就假定托管环境已支持。函数工具的参数 Schema 与最终答案的严格 JSON Schema 是不同功能，使用时请分别查阅说明。

### 基本请求约定

| 字段 | 使用方式 |
| --- | --- |
| `model` | 使用 `BB_MODEL_NAME` 注入的别名 |
| `messages` | 非空 Chat 消息数组，按本 Case 自行维护历史，不跨 Case 共享 |
| `store` | 显式发送 `false`，不使用共享服务端会话状态 |
| `n` | 省略或设为 `1`，不请求多个候选 |
| `max_completion_tokens` | 单次输出上限；取值及省略时的处理方式见官网最新规范。示例值不是比赛限额，不同时传其他输出限额字段 |

`max_tokens` 是旧字段，不作为新示例推荐；不得同时传 `max_tokens` 和 `max_completion_tokens`。`max_output_tokens` 是 Responses 字段，不用于 Chat。

请使用平台提供的模型地址与任务凭证，不要自行连接个人模型端点或外部服务。沙箱权限和允许使用的资源以比赛规则及官网最新规范为准。

## 3. Python 最小示例

示例使用 Python 3.11、`openai==2.54.0` 和 `httpx==0.28.1`。请在 `requirements.lock` 中固定依赖版本；如使用其他版本或框架，请检查其请求格式是否符合本指南。

```python
import os
from pathlib import Path
import httpx
from openai import OpenAI

with OpenAI(
    base_url=os.environ["BB_MODEL_BASE_URL"],
    api_key=Path(os.environ["BB_MODEL_TOKEN_FILE"]).read_text().strip(),
    max_retries=0,
    timeout=httpx.Timeout(620.0, connect=3.0),
    http_client=httpx.Client(
        trust_env=False, headers={"Accept-Encoding": "identity"}
    ),
) as client:
    result = client.chat.completions.create(
        model=os.environ["BB_MODEL_NAME"],
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_completion_tokens=128,
        store=False,
    )
    if result.choices[0].finish_reason != "stop":
        raise RuntimeError("Incomplete model response")
    print(result.choices[0].message.content)
```

**128 token 与 620 秒仅为示例值，不是比赛限额。** 请在提供上述配置的托管运行环境中使用此示例。它演示模型客户端初始化及一次调用；完整 Agent 还需要实现 worktree 修复，并按提交协议写出 `agent-result.json`。下载的 `model-access-example.py` 包含凭证和响应检查，可作为模型适配器的参考。

## 4. 函数工具与流式响应的处理

如使用官网当前支持的函数工具或流式响应，请遵循以下消息处理方式；具体可用参数和组合以官网最新规范为准。

1. 保留包含 `tool_calls` 的原始 assistant 消息。
2. Agent 验证工具名称、参数、路径和权限，执行允许的工具操作。
3. 用 `role: tool` 和原始 `tool_call_id` 回传每个结果，再沿用完整 `messages` 继续请求。

工具由 Agent 执行，模型只返回调用请求。请在执行前校验模型提供的参数，并遵守允许修改的路径和运行权限；不要直接执行未经检查的 shell 命令。沙箱不提供 Docker socket 或直接访问最终 Validator 的权限。

使用 SSE 时，请拼接文本和工具参数的增量，并在流结束后检查 `finish_reason`（根据阶段为 `stop` 或 `tool_calls`）及末尾用量信息。最后一个用量 chunk 的 `choices` 可以为空。若响应达到输出上限、拒答或中途断流，请按未完成响应处理，不要仅凭 HTTP 200 或部分文本判定调用成功。

## 5. 失败、重试与缓存

请关闭 SDK 自动重试（`max_retries=0`）。模型初始化或调用失败时，在日志中记录失败阶段、异常类型和 HTTP 状态，但不要记录凭证或敏感请求内容。若错误导致 Agent 无法继续，请以非零退出码结束，不要将失败标记为 `status: completed`。

| 状态 | 处理方式 |
| --- | --- |
| 400 / 413 | 检查参数、消息格式和请求/输出大小，按错误提示及官网当前公布的限制修正请求 |
| 401 / 403 | 检查平台配置、凭证文件是否可读及凭证是否有效；请勿替换为个人 API Key |
| 409 | 当前 Case 已有模型请求在执行，请勿并发提交新请求 |
| 429 | 当前任务已达到限制，或因用量未确认而暂停调用；停止新增请求 |
| 502 / 504 | 模型服务请求失败或超时，记录失败阶段及错误信息 |

超时或断流后，请保留错误信息，不要自动重试、新建会话或更换凭证绕过任务限制。如持续无法接入，请联系组织方，并提供提交 ID、运行 ID、失败阶段和脱敏后的错误信息。

缓存相关配置以官网最新规范为准，不要额外发送预热请求。可将稳定的指令和工具定义放在前面，随后追加当前 Case 的输入和工具结果；不必为了缓存加入无关内容。缓存命中并非每次都会发生，也不会增加可用请求次数。

## 6. 适配与测试步骤

`agents/model-hello/` v0.2.2 展示如何读取平台配置、调用模型并修改公开 hello Case，供接入时参考。该示例只针对 hello，不能作为通用修复方案；示例中的调用次数和限额不是比赛要求。

建议按以下顺序准备 Agent：

1. **适配模型配置。** 在模型适配器中读取 `BB_MODEL_BASE_URL`、`BB_MODEL_NAME` 和 `BB_MODEL_TOKEN_FILE`。如原先使用 `BB_LLM_ENDPOINT`、`OPENAI_API_BASE_URL` 等自定义变量，请自行完成映射；平台只提供本指南列出的三个配置。注意区分 SDK 的基地址与完整请求地址。
2. **本地检查并打包。** 在 Starter Kit 根目录执行 `./bb check --agent <Agent目录>` 和 `./bb package --agent <Agent目录>`。本地 `bb test`、`bb ready` 不提供官方模型通道；依赖模型的 Agent 需在托管环境中检查接入是否正常。
3. **上传并运行 Hosted Smoke Test。** 上传打包后的 ZIP，通过静态检查后运行 Hosted Smoke，检查模型初始化、Agent 执行及最终修复验证结果。Hosted Smoke 不计入正式排名。

在 Starter Kit 根目录运行 `./bb model-check`，可使用模拟响应检查内置模型示例的 SDK 调用、文件修改和错误处理。该命令不调用真实模型，也不测试你自行编写的 Agent；你提交的版本仍需通过 Hosted Smoke Test。

准备依赖镜像时可以下载所需依赖；Agent 正式运行时仍在隔离沙箱中，模型请求通过平台提供的通道完成。

Agent 退出后，平台会在干净 Case 上重放最终 worktree 的修改，独立构建并检查制品。模型调用成功不代表修复成功，请以最终验证结果为准。构建反馈的开放安排及限制，请查阅官网最新规范和比赛通知。

Development Validation 用于验证上传的已修复 worktree，不用于测试 Agent 的模型接入。

**已有提交如何处理：** 如果 Agent 已能读取上述配置，可在模型接入开放后对原版本重新运行 Hosted Smoke，无需重复上传。如果需要修改模型适配器，请打包并上传新版本。此前通过 Smoke 的结果不代表该版本已经完成模型接入测试。

各阶段的请求次数、输出上限、运行时长及其他资源限制以官网最新规范和比赛通知为准，同一阶段的所有队伍采用相同限制。请勿将示例参数当作上述限额。

## 参考

以下文档可帮助你了解 Chat 消息格式、工具调用和流式响应。接入 Build-Bench 时，请使用本指南中的平台配置，并遵守官网最新规范。

- [Build-Bench 模型接入规范（最新支持范围与限额）](https://matrix.cstcloud.cn/build-bench/submission.html?lang=zh#model-access)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)
- [OpenAI Chat streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)


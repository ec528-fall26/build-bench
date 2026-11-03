# 新版 Starter Kit 使用说明

本包为 **0.1.0-rc.5**，运行时镜像仍采用独立发布的 rc.3 版本。下载新包不等于官方模型服务已开放，启用时间以组织方公告为准。

## 本地检查

在 Linux Bash 或 Windows WSL2 + Docker 中进入解压目录：

```bash
./bb version
./bb doctor
./bb model-check
./bb check --agent ./agents/model-hello
./bb package --agent ./agents/model-hello --output dist/buildbench-model-hello-v0.2.1.zip
```

`model-check` 仅测试内置示例：用模拟 HTTP 检查 SDK 请求、初始化、受限编辑和报错。不调用真实模型、不执行目标构建、不代表你自己的 Agent 已通过 Hosted Smoke。依赖镜像首次准备可下载固定包，测试容器运行时仍禁网。

原有 `bb demo`、`bb bootstrap`、`bb ready` 继续适用于不依赖官方模型通道的本地示例。`bb ready` 包含本地运行测试，不能作为模型依赖 Agent 的官方模型联调入口。

## 适配自己的 Agent

阅读 [模型接入规范](guide.zh.md) 与 [配置示例](example.py)。平台启动入口前注入：

- `BB_MODEL_BASE_URL`：包含 `/v1` 的基地址。
- `BB_MODEL_NAME`：统一模型别名。
- `BB_MODEL_TOKEN_FILE`：只读任务凭证文件路径。

将这三项映射到客户端，使用 Chat Completions。不要写死供应商地址或实际模型，不上传或打印个人 Key。模型接入失败时，请记录失败阶段、异常类型和 HTTP 状态；若 Agent 无法继续，请以非零退出码结束。

本地 `bb test` 不注入官方通道；只在宿主机设置这些变量不会让沙箱得到通道。先本地 check、package，托管服务启用后再运行 Hosted Smoke。代码未改可重新测试原提交，修改代码后才上传新版本。

## 示例及限额

`agents/model-hello/` v0.2.1 仅演示公开 hello：一次模型生成选择安全编辑，Agent 退出后平台独立构建和验证产物。它不是通用基线，本包不提供运行中目标构建客户端。示例请求次数、输出上限、超时不是正式规则。

Smoke 测试限额和正式每 Case 预算由组织方分别规定；正式预算及反馈安排统一告知所有队伍。下载本包不包含本地开发 API 凭证或额度。

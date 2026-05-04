# Small OpenAI-Compatible Agent

这个示例使用 OpenAI chat completions 协议，可以指向 DeepSeek 的 OpenAI-compatible API。

## 环境变量

可以直接在项目根目录创建 `.env`，示例：

```bash
DEEPSEEK_API_KEY=你的 key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_THINKING=disabled
```

示例 agent 会自动读取当前工作目录下的 `.env`。已经存在的 shell 环境变量优先，不会被 `.env` 覆盖。
DeepSeek base URL 下默认会发送 `thinking: {"type": "disabled"}`，显式关闭 thinking。

DeepSeek 写法：

```bash
export DEEPSEEK_API_KEY="你的 key"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-chat"
export DEEPSEEK_THINKING="disabled"
```

OpenAI-compatible 通用写法：

```bash
export OPENAI_API_KEY="你的 key"
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_MODEL="deepseek-chat"
export OPENAI_THINKING="disabled"
```

如果两组都设置，`OPENAI_*` 优先。

## 运行

```bash
uv run --python 3.11 agent-os-small-agent "读取 pyproject.toml 里的项目名，并用一句话回答。"
```

如果要查看每次真实传给 LLM 的上下文交互，加 `--trace`：

```bash
uv run --python 3.11 agent-os-small-agent --trace "读取 pyproject.toml 里的项目名，并用一句话回答。"
```

trace 会打印：

- `system`：完整渲染后的 LLM 可见上下文。
- `messages`：当前 active window 里的 provider messages。
- `tools`：本轮传给 provider 的工具 schema。
- `response`：provider 返回后被 SDK 标准化的 content 和 tool calls。

也可以直接运行模块：

```bash
uv run --python 3.11 python -m agentos.examples.small_openai_agent "读取 pyproject.toml 里的项目名，并用一句话回答。"
```

## 当前能力

- 使用 `OpenAICompatibleProvider` 调 `/chat/completions`。
- 通过 provider tool call 调用内置 `read_file` 工具。
- 工具结果写回 `MessageRuntime` 后继续请求模型生成最终回答。
- 默认只允许读取当前项目目录内的文件。

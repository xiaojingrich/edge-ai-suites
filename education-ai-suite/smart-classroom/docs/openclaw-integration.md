# OpenClaw Integration - Function Calling Flow

## Architecture

```
┌──────────────┐       ┌────────────────────┐       ┌──────────────────┐
│   OpenClaw   │──────▶│  vLLM              │       │  MCP Server      │
│              │◀──────│  (Qwen2.5-7B)      │       │  (smart-classroom)│
│  - Skill     │       │  :9905             │       │  :8100           │
│  - MCP Client│──────────────────────────────────▶│  - list_sessions  │
│  - Loop      │◀──────────────────────────────────│  - read_session_  │
│              │       └────────────────────┘       │    files          │
└──────────────┘                                    └──────────────────┘
```

## End-to-End Flow

```
┌─ OpenClaw ──────────────────────────────────────────────────────────────┐
│                                                                         │
│  1. 用户: "生成课堂报告"                                                  │
│     ↓                                                                   │
│  2. Skill 触发: classroom-report                                        │
│     ↓                                                                   │
│  3. OpenClaw 构建请求发给 vLLM:                                           │
│     {                                                                   │
│       "messages": [{"role":"system", "content":"你是课堂评估分析师..."},    │
│                    {"role":"user", "content":"生成课堂报告"}],             │
│       "tools": [                                                        │
│         {"type":"function", "function":{"name":"list_sessions",...}},    │
│         {"type":"function", "function":{"name":"read_session_files",...}}│
│       ]                                                                 │
│     }                                                                   │
│     ↓                                                                   │
└─────┼───────────────────────────────────────────────────────────────────┘
      │ HTTP POST /v1/chat/completions
      ↓
┌─ vLLM (Qwen2.5-7B-Instruct) ───────────────────────────────────────────┐
│                                                                         │
│  4. vLLM 内部处理 tools schema + 生成                                     │
│     ↓                                                                   │
│  5. 模型决定调用工具，vLLM 原生解析 tool call                               │
│     ↓                                                                   │
│  6. 返回 OpenAI 格式响应:                                                 │
│     {"tool_calls":[{"function":{"name":"list_sessions"}}],              │
│      "finish_reason":"tool_calls"}                                      │
│                                                                         │
└─────┼───────────────────────────────────────────────────────────────────┘
      │ 响应
      ↓
┌─ OpenClaw ──────────────────────────────────────────────────────────────┐
│                                                                         │
│  7. 收到 tool_calls → 执行 MCP tool: list_sessions()                     │
│     ↓                                                                   │
└─────┼───────────────────────────────────────────────────────────────────┘
      │ MCP 调用
      ↓
┌─ MCP Server (smart-classroom:8100) ─────────────────────────────────────┐
│                                                                         │
│  8. list_sessions() → 返回 {"sessions":[{"session_id":"20260601-...",   │
│     "files":["transcription.txt","summary.md",...]}]}                   │
│                                                                         │
└─────┼───────────────────────────────────────────────────────────────────┘
      │ 结果
      ↓
┌─ OpenClaw ──────────────────────────────────────────────────────────────┐
│                                                                         │
│  9. 把 tool 结果塞入 messages 继续请求 vLLM:                               │
│     messages: [...之前的...,                                             │
│       {"role":"assistant","tool_calls":[...]},                           │
│       {"role":"tool","tool_call_id":"call_xxx",                          │
│        "content":"{\"sessions\":[...]}"}                                │
│     ]                                                                   │
│     ↓ 再次 POST /v1/chat/completions                                    │
│                                                                         │
│  10. 模型返回下一个 tool_call: read_session_files(...)                    │
│      ↓                                                                  │
│  11. OpenClaw 执行 MCP → 拿到文件内容                                     │
│      ↓                                                                  │
│  12. 再次发给 vLLM，这次模型有了所有数据                                     │
│      ↓                                                                  │
│  13. 模型返回最终报告文本 (finish_reason: "stop")                          │
│      ↓                                                                  │
│  14. OpenClaw 展示给用户                                                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Detailed Steps

### Step 1: User Triggers Skill

User sends a message that matches the `classroom-report` skill trigger:

```
User: "生成课堂报告"
```

OpenClaw matches this to the `classroom-report` skill and starts the function calling loop.

### Step 2: First LLM Request (with tools)

OpenClaw sends to vLLM:

```json
POST http://<vllm-host>:9905/v1/chat/completions

{
  "model": "Qwen/Qwen2.5-7B-Instruct",
  "messages": [
    {"role": "system", "content": "<SKILL.md content>"},
    {"role": "user", "content": "生成课堂报告"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "list_sessions",
        "description": "List all available classroom sessions with their available data files.",
        "parameters": {"type": "object", "properties": {}}
      }
    },
    {
      "type": "function",
      "function": {
        "name": "read_session_files",
        "description": "Read one or more data files from a classroom session.",
        "parameters": {
          "type": "object",
          "properties": {
            "session_id": {"type": "string"},
            "filenames": {"type": "array", "items": {"type": "string"}}
          },
          "required": ["session_id", "filenames"]
        }
      }
    }
  ]
}
```

### Step 3: vLLM Responds with Tool Call

vLLM natively parses the model's tool call output and returns OpenAI-format response:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_a1b2c3d4",
        "type": "function",
        "function": {
          "name": "list_sessions",
          "arguments": "{}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

### Step 4: OpenClaw Executes MCP Tool

OpenClaw sees `finish_reason: "tool_calls"`, calls the MCP Server:

```
MCP call: list_sessions()
```

MCP Server returns:

```json
{
  "sessions": [
    {
      "session_id": "20260601-102514-d075",
      "files": ["transcription.txt", "teacher_transcription.txt", "summary.md", "mindmap.mmd", "topics.json", "va/class_statistics.json"]
    }
  ]
}
```

### Step 5: Second LLM Request (with tool result)

OpenClaw appends tool result to messages and sends again:

```json
{
  "model": "Qwen/Qwen2.5-7B-Instruct",
  "messages": [
    {"role": "system", "content": "<SKILL.md content>"},
    {"role": "user", "content": "生成课堂报告"},
    {"role": "assistant", "content": null, "tool_calls": [{"id": "call_a1b2c3d4", "function": {"name": "list_sessions", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_a1b2c3d4", "content": "{\"sessions\":[{\"session_id\":\"20260601-102514-d075\",\"files\":[...]}]}"}
  ],
  "tools": [...]
}
```

### Step 6: vLLM Requests File Contents

Response:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_e5f6g7h8",
        "type": "function",
        "function": {
          "name": "read_session_files",
          "arguments": "{\"session_id\": \"20260601-102514-d075\", \"filenames\": [\"summary.md\", \"teacher_transcription.txt\", \"va/class_statistics.json\", \"topics.json\"]}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

### Step 7: OpenClaw Executes MCP Tool Again

```
MCP call: read_session_files("20260601-102514-d075", ["summary.md", "teacher_transcription.txt", "va/class_statistics.json", "topics.json"])
```

MCP Server returns all file contents in one response.

### Step 8: Third LLM Request (with all data)

OpenClaw appends file contents to messages and sends the final request.

### Step 9: vLLM Generates Final Report

Model now has all classroom data in context. Response:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "# 课堂评估报告\n\n## 1. 统计概览\n| 指标 | 数值 |\n..."
    },
    "finish_reason": "stop"
  }]
}
```

### Step 10: OpenClaw Delivers to User

`finish_reason: "stop"` → loop ends → display report to user.

## Summary

| Round | Direction | Content |
|-------|-----------|---------|
| 1 | OpenClaw → vLLM | User message + tools schema |
| 1 | vLLM → OpenClaw | `tool_calls: list_sessions` |
| 1 | OpenClaw → MCP | Execute `list_sessions()` |
| 2 | OpenClaw → vLLM | + tool result (session list) |
| 2 | vLLM → OpenClaw | `tool_calls: read_session_files` |
| 2 | OpenClaw → MCP | Execute `read_session_files(...)` |
| 3 | OpenClaw → vLLM | + tool result (file contents) |
| 3 | vLLM → OpenClaw | Final report text (`stop`) |

Typically **3 LLM rounds** for a full report generation.

## Deployment

### 1. vLLM (LLM Service with Function Calling)

vLLM provides native function calling support — no custom parsing needed.

#### Install

```bash
pip install vllm
```

#### Start (from HuggingFace model)

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --host 0.0.0.0 \
  --port 9905 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.9
```

#### Start (from local model path)

```bash
vllm serve /path/to/Qwen2.5-7B-Instruct \
  --host 0.0.0.0 \
  --port 9905 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.9
```

#### Parameters

| Parameter | Purpose |
|-----------|---------|
| `--enable-auto-tool-choice` | Enable function calling support |
| `--tool-call-parser hermes` | Parser format for Qwen2.5 tool calls |
| `--max-model-len 8192` | Max context length (adjust as needed) |
| `--gpu-memory-utilization 0.9` | GPU memory usage ratio |

#### Verify

```bash
curl http://localhost:9905/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "messages": [{"role": "user", "content": "hello"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "list_sessions",
          "description": "List all available classroom sessions",
          "parameters": {"type": "object", "properties": {}}
        }
      }
    ]
  }'
```

When the model decides to call a tool, the response will contain `tool_calls` directly — no manual parsing required.

### 2. Smart Classroom (MCP Server + Data Pipeline)

```bash
cd education-ai-suite/smart-classroom
python main.py
```

This starts:
- FastAPI main service (port 8000)
- MCP Server (port 8100)
- Media service (video analytics)

### 3. OpenClaw

#### Install Skills

Copy skill definitions from smart-classroom to the OpenClaw agent workspace:

```bash
cp -r education-ai-suite/smart-classroom/openclaw-skills/classroom-report <openclaw-workspace>/skills/
cp -r education-ai-suite/smart-classroom/openclaw-skills/classroom-homework <openclaw-workspace>/skills/
cp -r education-ai-suite/smart-classroom/openclaw-skills/classroom-lesson-prep <openclaw-workspace>/skills/
```

Where `<openclaw-workspace>` is your OpenClaw agent workspace directory (e.g., `~/.openclaw/workspace` or a custom path).

Verify skills are loaded:

```bash
openclaw skills list
```

#### Configure connections

## Configuration

### OpenClaw MCP Server Config

```json
{
  "mcp": {
    "servers": {
      "smart-classroom": {
        "url": "http://<smart-classroom-host>:8100/mcp",
        "transport": "sse"
      },
      "docx-tools": {
        "command": "npx",
        "args": ["docx-mcp-server"]
      }
    }
  }
}
```

- `smart-classroom`: Provides classroom session data (list_sessions, read_session_files)
- `docx-tools`: Provides docx template parsing and filling for report generation (third-party MCP server, replace with actual package name)

### OpenClaw LLM Provider Config

```json
{
  "providers": {
    "smart-classroom-llm": {
      "type": "openai-compatible",
      "baseUrl": "http://<vllm-host>:9905/v1",
      "model": "Qwen/Qwen2.5-7B-Instruct"
    }
  }
}
```

### Ports

| Service | Host | Default Port | Env Variable |
|---------|------|-------------|--------------|
| Smart Classroom API | smart-classroom machine | 8000 | — |
| MCP Server | smart-classroom machine | 8100 | `MCP_SERVER_PORT` |
| vLLM (LLM Service) | OpenClaw/GPU machine | 9905 | — |

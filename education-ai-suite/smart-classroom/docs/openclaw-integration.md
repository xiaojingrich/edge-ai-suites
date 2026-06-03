# OpenClaw Integration - Function Calling Flow

## Architecture

```
┌──────────────┐       ┌────────────────────┐       ┌──────────────────────┐
│   OpenClaw   │──────▶│  OVMS              │       │  MCP Server          │
│              │◀──────│  (Qwen2.5-7B, OV)  │       │  (smart-classroom)   │
│  - Skill     │       │  :8000/v3          │       │  :8100 (SSE)         │
│  - MCP Client│──────────────────────────────────▶│  - list_sessions     │
│  - ReAct Loop│◀──────────────────────────────────│  - read_session_files│
│              │       └────────────────────┘       │  - get_teaching_stats│
└──────────────┘                                    └──────────────────────┘

Agent 完全运行在 OpenClaw 侧；Smart Classroom 只作为 MCP 工具服务器，
不再做编排/推理。本地 LLM 用 OpenVINO Model Server (OVMS)，在 Intel GPU 上
提供原生 function calling（部署时配 tool_parser: hermes3 解析 Qwen2.5 tool call）。
```

## End-to-End Flow

```
┌─ OpenClaw ──────────────────────────────────────────────────────────────┐
│                                                                         │
│  1. 用户: "生成课堂报告"                                                  │
│     ↓                                                                   │
│  2. Skill 触发: classroom-report                                        │
│     ↓                                                                   │
│  3. OpenClaw 构建请求发给 OVMS:                                          │
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
      │ HTTP POST /v3/chat/completions
      ↓
┌─ OVMS (Qwen2.5-7B-Instruct, OpenVINO) ─────────────────────────────────┐
│                                                                         │
│  4. OVMS 内部处理 tools schema + 生成                                     │
│     ↓                                                                   │
│  5. 模型决定调用工具，tool_parser(hermes3) 解析 tool call                  │
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
│  9. 把 tool 结果塞入 messages 继续请求 OVMS:                              │
│     messages: [...之前的...,                                             │
│       {"role":"assistant","tool_calls":[...]},                           │
│       {"role":"tool","tool_call_id":"call_xxx",                          │
│        "content":"{\"sessions\":[...]}"}                                │
│     ]                                                                   │
│     ↓ 再次 POST /v3/chat/completions                                    │
│                                                                         │
│  10. 模型返回下一个 tool_call: read_session_files(...)                    │
│      ↓                                                                  │
│  11. OpenClaw 执行 MCP → 拿到文件内容                                     │
│      ↓                                                                  │
│  12. 再次发给 OVMS，这次模型有了所有数据                                    │
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

OpenClaw sends to OVMS:

```json
POST http://<ovms-host>:8000/v3/chat/completions

{
  "model": "Qwen2.5-7B-Instruct",
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

### Step 3: OVMS Responds with Tool Call

With `tool_parser: hermes3` configured, OVMS parses the model's tool call output and returns an OpenAI-format response:

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
  "model": "Qwen2.5-7B-Instruct",
  "messages": [
    {"role": "system", "content": "<SKILL.md content>"},
    {"role": "user", "content": "生成课堂报告"},
    {"role": "assistant", "content": null, "tool_calls": [{"id": "call_a1b2c3d4", "function": {"name": "list_sessions", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_a1b2c3d4", "content": "{\"sessions\":[{\"session_id\":\"20260601-102514-d075\",\"files\":[...]}]}"}
  ],
  "tools": [...]
}
```

### Step 6: OVMS Requests File Contents

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

### Step 9: OVMS Generates Final Report

Model now has all classroom data in context. It returns the report as Markdown text:

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

`finish_reason: "stop"` → loop ends → display the Markdown report to user.

## Summary

| Round | Direction | Content |
|-------|-----------|---------|
| 1 | OpenClaw → OVMS | User message + tools schema |
| 1 | OVMS → OpenClaw | `tool_calls: list_sessions` |
| 1 | OpenClaw → MCP | Execute `list_sessions()` |
| 2 | OpenClaw → OVMS | + tool result (session list) |
| 2 | OVMS → OpenClaw | `tool_calls: read_session_files` |
| 2 | OpenClaw → MCP | Execute `read_session_files(...)` |
| 3 | OpenClaw → OVMS | + tool result (file contents) |
| 3 | OVMS → OpenClaw | Final report Markdown (`stop`) |

Typically **3 LLM rounds** for a full report generation.

## Deployment

### 1. OVMS (OpenVINO Model Server with Function Calling)

OVMS serves the LLM on Intel GPU/CPU with an OpenAI-compatible API and supports native function calling. The OpenAI-compatible chat endpoint is **`/v3/chat/completions`**.

> ⚠️ **Function calling requires two things on OVMS:**
> 1. A **Python-enabled** OVMS (tools are *not* supported in a no-Python configuration).
> 2. A **`tool_parser`** configured on the servable (e.g. `hermes3` for Qwen2.5/Qwen3). Without it, the model's tool call is returned as plain text instead of structured `tool_calls`.
>
> The `tool_parser` is set in the servable's `graph.pbtxt` (`node_options`), not as a CLI flag. The easiest way is to let the export script generate it (see below).

#### Export the model with a tool parser

Use the OVMS export script (`demos/common/export_models/export_model.py`) to download/convert the model **and** write a `graph.pbtxt` that includes the tool parser:

```bash
python export_model.py text_generation \
  --source_model Qwen/Qwen2.5-7B-Instruct \
  --model_repository_path models \
  --target_device GPU \
  --tool_parser hermes3 \
  --enable_tool_guided_generation
```

This produces `models/Qwen/Qwen2.5-7B-Instruct/` with a `graph.pbtxt` containing `tool_parser: "hermes3"` and `enable_tool_guided_generation: true`.

#### Start OVMS (Docker, Intel GPU)

```bash
docker run --user $(id -u):$(id -g) -d \
  --device /dev/dri \
  --group-add=$(stat -c "%g" /dev/dri/render* | head -n 1) \
  --rm -p 8000:8000 \
  -v $(pwd)/models:/models:rw \
  openvino/model_server:latest-gpu \
  --model_repository_path /models \
  --model_name Qwen2.5-7B-Instruct \
  --model_path /models/Qwen/Qwen2.5-7B-Instruct \
  --rest_port 8000 \
  --target_device GPU
```

> Use the model name you want OpenClaw to reference as `--model_name` — it must match OpenClaw's `models[].id` and `model.primary`.

#### Key parameters

| Parameter | Purpose |
|-----------|---------|
| `--target_device GPU` | Run on Intel GPU (`/dev/dri`). Use `CPU` to run on CPU. |
| `--rest_port 8000` | REST port; OpenAI-compatible API served at `/v3/...`. |
| `tool_parser: hermes3` (in `graph.pbtxt`) | Extract `tool_calls` from Qwen2.5/Qwen3 output. **Required for function calling.** |
| `enable_tool_guided_generation: true` | Push the model to emit tool calls matching the `tools` schema. |

#### Verify

```bash
# 1) model ready
curl http://localhost:8000/v1/config

# 2) function calling — note the /v3 path
curl http://localhost:8000/v3/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen2.5-7B-Instruct",
    "messages": [{"role": "user", "content": "list my classroom sessions"}],
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

A correct setup returns `"finish_reason": "tool_calls"` with a `tool_calls` array. If you instead get the tool call as plain text in `content`, the `tool_parser` is missing or the servable was built without Python.

> **Endpoint note:** OVMS uses `/v3/chat/completions` (`messages` + `tools`). Make sure OpenClaw's provider `baseUrl` ends in `/v3` (see Configuration). Sending a legacy `/v1/completions`-style `{"prompt": "..."}` body drops `tools` and **function calling never happens** — an endpoint/format problem, not a parser problem.

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

All of the following goes into `~/.openclaw/openclaw.json` (one file). Shown split by concern.

### LLM Provider (OVMS)

OVMS is a custom OpenAI-compatible provider. Register it under `models.providers` with `api: "openai-completions"`. **The `baseUrl` must end in `/v3`** (OVMS's OpenAI-compatible path), not `/v1`.

```json5
{
  "models": {
    "mode": "merge",
    "providers": {
      "ovms": {
        // OVMS OpenAI-compatible endpoint — MUST end in /v3.
        // OpenClaw's "openai-completions" API sends POST <baseUrl>/chat/completions
        // with a `messages` body (NOT a legacy `prompt` body).
        "baseUrl": "http://<ovms-host>:8000/v3",
        "apiKey": "${OVMS_API_KEY}",
        "api": "openai-completions",
        "timeoutSeconds": 300,
        "models": [
          {
            // id MUST match OVMS --model_name and the agent model.primary
            "id": "Qwen2.5-7B-Instruct",
            "name": "Qwen2.5-7B-Instruct (OVMS)",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 32768,
            "maxTokens": 8192
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "ovms/Qwen2.5-7B-Instruct" },
      "skipBootstrap": true
    }
  }
}
```

Set `OVMS_API_KEY` in `~/.openclaw/.env` (any non-empty value if OVMS does not enforce auth):

```bash
OVMS_API_KEY=ovms-local
```

> **Common pitfall (the `prompt` vs `messages` bug):** OpenClaw uses `models.providers.<id>` with
> `"api": "openai-completions"`. There is **no** top-level `providers` block and **no** `"type": "openai-compatible"`
> field — using those produces a malformed/legacy request shape. The correct `openai-completions` API always
> POSTs to `<baseUrl>/chat/completions` with `messages` + `tools` (here `/v3/chat/completions`), which is what function calling requires.

### MCP Server (Smart Classroom tools)

```json5
{
  "mcp": {
    "servers": {
      "smart-classroom": {
        // Smart Classroom MCP server runs SSE transport (see mcp_server/server.py, main.py).
        // SSE endpoint path is /sse.
        "url": "http://<smart-classroom-host>:8100/sse",
        "transport": "sse"
      }
    }
  }
}
```

- `smart-classroom`: classroom session data + stats (`list_sessions`, `read_session_files`, `get_teaching_stats`)

> **Report output format:** reports are generated as **Markdown** by the model and returned directly to the user — no docx MCP server is required.
>
> If you later want `.docx` output, add a docx MCP server under `mcp.servers`. Note (verified 2026-06-03) that no existing docx MCP server has a native `{placeholder}` template engine; the closest options are [`@knorq/docx-mcp-server`](https://www.npmjs.com/package/@knorq/docx-mcp-server) (npx, active — copy template + find/replace each `{field}`) or building a tiny stdio MCP server around [`docxtpl`](https://pypi.org/project/docxtpl/) for robust `{{field}}` filling.

### Ports

| Service | Host | Default Port | Env Variable |
|---------|------|-------------|--------------|
| Smart Classroom API | smart-classroom machine | 8000 | — |
| MCP Server (SSE) | smart-classroom machine | 8100 | `MCP_SERVER_PORT` |
| OVMS (LLM Service, `/v3`) | Intel GPU machine | 8000 | `--rest_port` |

> Smart Classroom API and OVMS both default to `8000`. If you run them on the **same host**, change one (e.g. OVMS `--rest_port 9000` and set the provider `baseUrl` to `http://<host>:9000/v3`) to avoid a clash.

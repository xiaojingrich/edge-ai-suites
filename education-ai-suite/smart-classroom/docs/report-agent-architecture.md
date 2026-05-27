# Smart Classroom Multi-Agent Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    OpenClaw (唯一入口, 路由 + 编排)                              │
│                                                                                 │
│  ┌─── Provider (云端) ──────────────┐  ┌─── Provider (本地) ──────────────────┐│
│  │  Cloud LLM (GPT-4o / Claude)     │  │  Smart Classroom /v1/chat/completions ││
│  │  精准意图识别 + Skill 选择       │  │  复用 Qwen2.5-7B, 基本意图识别       ││
│  └───────────────────────────────────┘  └────────────────────────────────────┘│
│                                                                                 │
│  Skills (SKILL.md) — 教 LLM 调用哪个 API:                                      │
│  ├── classroom-report     → POST /agent/chat {output_format}                    │
│  ├── classroom-homework   → POST /homework/... (future)                         │
│  └── classroom-lesson-prep → POST /lesson-prep/... (future)                     │
│                                                                                 │
│  OpenClaw 职责:                                                                 │
│  ├── 1. 理解用户问题                                                           │
│  ├── 2. 选择正确的 Skill                                                       │
│  ├── 3. 决定 output_format (report vs chat)                                    │
│  ├── 4. 调用 Smart Classroom 对应 endpoint                                     │
│  └── 5. 管理对话记忆、conversation_id                                          │
│                                                                                 │
│  ⚠️  只有用户问题经过 OpenClaw，原始课堂数据不出设备                           │
│                                                                                 │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │ HTTP (localhost)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Smart Classroom (执行层, 不做路由)                             │
│                                                                                 │
│  API Endpoints:                                                                 │
│  ├── POST /agent/chat        ← 学情Agent (多轮对话, 支持 output_format)         │
│  ├── POST /generate-report   ← 学情Agent (单次报告)                             │
│  ├── GET  /report/{id}       ← 获取已生成的报告                                │
│  ├── POST /homework/...      ← 作业Agent (future)                              │
│  └── POST /lesson-prep/...   ← 备课Agent (future)                              │
│                                                                                 │
│  Each Agent:                                                                    │
│  ├── Local 7B LLM (Qwen2.5-7B-Instruct, OpenVINO)                             │
│  ├── 只读取已有数据 (ReAct) + 分析推理 + 文本生成                               │
│  ├── 不生成原始数据 (ASR/Summary/Mindmap 由主 pipeline 完成)                    │
│  ├── 不做意图分析 — output_format 由 OpenClaw 传入                              │
│  └── 无数据时返回提示，不报错                                                   │
│                                                                                 │
│  Local Data (Private):                                                          │
│  └── transcription.txt, summary.md, front_posture.txt, topics.json, ...         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Report Agent 职责边界

### 做什么 (Read + Analyze)

| 工具 | 类型 | 说明 |
|------|------|------|
| `get_session_metadata` | READ | 查看 session 有哪些已生成的文件 |
| `get_class_report` | READ | 读取已生成的报告 (`class_report.md`)，追问时避免重新收集 |
| `get_transcription` | READ | 读取 ASR 转录文本 |
| `get_class_summary` | READ | 读取 AI 生成的课堂摘要 |
| `get_mindmap` | READ | 读取已生成的思维导图 |
| `get_topic_segmentation` | READ | 读取主题分段数据 |
| `get_class_statistics` | READ | 读取 `va/class_statistics.json` (课后由 VA pipeline 停止时自动保存) |
| `get_memory` | READ | 读取跨 session 历史记忆 |
| `save_memory` | MEMORY | 保存分析发现供未来 session 使用 |
| `skill_engagement_analysis` | SKILL | 基于 `class_statistics.json` 分析参与度 (通过 get_class_statistics 间接读取) |
| `skill_content_analysis` | SKILL | 分析教学目标和知识覆盖 |
| `skill_quiz_generation` | SKILL | 根据课堂内容生成测验题 |
| `skill_teacher_behavior` | SKILL | 分析教师行为和教学风格 |
| `skill_video_slice_summary` | SKILL | 识别关键教学片段 |
| `skill_ocr_board_analysis` | SKILL | 分析板书/PPT 内容 |
| `generate_final_report` | CONTROL | 结束数据收集，进入生成阶段 |

### 不做什么 (由主 Pipeline 在课中/课后完成)

| 数据 | 谁生成 | 何时生成 |
|------|--------|----------|
| `transcription.txt` | ASR Pipeline | 课堂中 (实时) |
| `summary.md` | Summarizer | 课后 (自动) |
| `mindmap.mmd` | Mindmap Generator | 课后 (自动) |
| `topics.json` | Content Segmentation | 课后 (自动) |
| `va/front_posture.txt` | Video Analytics | 课堂中 (实时) |
| `va/class_statistics.json` | VA Pipeline 停止时自动保存 | 课后 (停止 VA 时) |

### 无数据处理

如果 session 目录中所有关键文件都不存在（ReAct 循环未收集到任何 observation），Agent 直接返回：

- 中文: "当前无课堂记录数据，请先完成一节课的录制。"
- 英文: "No classroom recording data available. Please complete a class session first."

如果部分数据缺失，Agent 跳过缺失部分，基于已有数据生成报告/回答。

---

## Data Privacy Boundary

```
┌─────────────────────────────────────────────────────────────┐
│              What Goes Through OpenClaw                       │
│                                                             │
│  ✅ User's question text: "分析学生参与度"                  │
│  ✅ output_format hint: "chat" or "report"                  │
│  ✅ session_id, conversation_id (metadata)                  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│              What Stays Local (NEVER leaves device)          │
│                                                             │
│  🔒 Raw transcription (teacher/student speech)              │
│  🔒 Student names and identities                            │
│  🔒 Video analytics data (pose, movement)                   │
│  🔒 Classroom recordings (audio/video files)                │
│  🔒 Generated reports and analysis results                  │
│  🔒 Agent memory (historical observations)                  │
│  🔒 OCR extracted content                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## OpenClaw Provider Fallback (云端/本地)

```jsonc
// openclaw.json 配置:
{
  "gateway": {
    "mode": "local",                         // local = 不暴露外网
    "bind": "loopback",
    "auth": { "mode": "token", "token": "..." }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "openai": {},                          // ← Provider A (云端)
      "smart-classroom": {                   // ← Provider B (本地, 复用已有 7B 模型)
        "baseUrl": "http://127.0.0.1:8000",
        "apiKey": "local",
        "api": "openai-completions",
        "models": []
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "openai/gpt-4o",
        "fallbacks": ["smart-classroom/Qwen2.5-7B-Instruct"]
      },
      "workspace": "/home/edge/.openclaw/workspace-classroom",
      "skipBootstrap": true
    },
    "list": [
      {
        "id": "classroom",
        "default": true,
        "workspace": "/home/edge/.openclaw/workspace-classroom",
        "model": {
          "primary": "openai/gpt-4o",
          "fallbacks": ["smart-classroom/Qwen2.5-7B-Instruct"]
        },
        "identity": {
          "name": "学情助手",
          "theme": "classroom analysis assistant",
          "emoji": "📊"
        },
        "tools": {
          "allow": ["web_fetch", "session_status"]
        }
      }
    ]
  }
}
```

**本地 Provider 说明:**
- Smart Classroom 已内置 OpenAI-compatible endpoint: `POST /v1/chat/completions`
- 复用已加载的 Qwen2.5-7B-Instruct 模型，无需额外安装其他模型服务
- 仅供外部调用方 (OpenClaw) 使用；Smart Classroom 内部组件直接调用 Python 模型实例（避免重复 load/unload 开销）
- 通过 acquire/release 模式与 ReportAgent 共享 GPU，不会冲突
  (OpenClaw 意图识别 → release → HTTP 到 /agent/chat → ReportAgent acquire，天然串行)

**Failover 机制 (OpenClaw 内置):**
- 云端: `openai/gpt-4o` 读 SKILL.md → 精准判断意图 + output_format → 调 /agent/chat
- 本地: 自动切 `smart-classroom/Qwen2.5-7B-Instruct` → 基本判断意图 → 调 /agent/chat
- 触发条件: 网络不可达、HTTP 401/403/429、请求超时
- 恢复: 带 cooldown + exponential backoff 自动切回 primary
- "意图识别" = 选择匹配的 SKILL.md + 决定 output_format，不是独立的路由组件

---

## Report Agent 完整调用链

以 OpenClaw 收到 "分析学生参与度" 为例：

```
用户 → OpenClaw
       │
       ├── LLM 读取 classroom-report SKILL.md
       ├── 判断: 这是学情问题, output_format = "chat"
       └── 执行 Skill 中的调用指令:
           curl POST http://localhost:8000/agent/chat
           body: {message: "分析学生参与度", output_format: "chat"}
           (session_id 未传, 由后端自动取最新)

│
├── api/endpoints.py: agent_chat()
│   ├── session_id = request.session_id or get_latest_session_id()
│   ├── ConversationManager.create_conversation() → conv_id
│   ├── conv_manager.add_message(conv_id, "user", message)
│   │
│   └── Pipeline(session_id).run_report(query, output_format="chat")
│       │
│       └── ReportAgent(session_id, user_query, output_format="chat")
│           │
│           ├── model.acquire_model()  ← 加载 7B 到 GPU
│           │
│           ├── _run_react_loop()  ← PHASE 1: 数据收集 (max 10, 目标 3-5 步)
│           │   │
│           │   ├── Step 1: LLM → "查 session metadata"
│           │   │   └── tools.execute_tool("get_session_metadata")
│           │   │       → 发现 class_report.md 不存在, 但有 VA 数据
│           │   │
│           │   ├── Step 2: LLM → "有 VA 数据，查统计"
│           │   │   └── tools.execute_tool("get_class_statistics")
│           │   │
│           │   ├── Step 3: LLM → "数据够了"
│           │   │   └── tools.execute_tool("generate_final_report") → break
│           │   │
│           │   (如果 class_report.md 已存在且是追问:
│           │    Step 2: get_class_report → Step 3: generate, 仅 3 步)
│           │
│           ├── Output Format Decision  ← PHASE 1.5
│           │   output_format_hint == "chat" (from OpenClaw)
│           │   → _build_chat_prompt(observations)
│           │
│           ├── model.generate(prompt, stream=True)  ← PHASE 2
│           │   └── yield tokens → SSE → OpenClaw → 用户
│           │
│           └── model.release_model()  ← 释放 GPU
│
└── StreamingResponse → OpenClaw → 用户看到结果
```

### 文件级调用关系

```
api/endpoints.py
  └── POST /agent/chat (entry point, called by OpenClaw)
      │
      └── pipeline.py
          └── run_report(query, output_format)
              │
              └── components/report_agent/
                  ├── report_agent.py        ← ReAct 主循环 + 流式生成
                  │   ├── _run_react_loop()  ← 7B 自主决定调哪些工具
                  │   └── generate_report()  ← acquire → react → generate → release
                  │
                  ├── tools.py               ← 16 个工具 (READ/MEMORY/SKILL/CONTROL)
                  │   └── execute_tool(name) → observation string
                  │
                  ├── skills/                ← 6 个 LLM 分析技能
                  │   ├── engagement_analysis.py
                  │   ├── quiz_generation.py
                  │   ├── content_analysis.py
                  │   ├── ocr_board_analysis.py
                  │   ├── video_slice_summary.py
                  │   └── teacher_behavior.py
                  │
                  ├── prompts.py             ← Prompt 模板
                  │   ├── REACT_SYSTEM_PROMPT ← ReAct 循环用
                  │   ├── REPORT_GENERATION_PROMPT ← output_format="report" 时用
                  │   └── CHAT_RESPONSE_PROMPT    ← output_format="chat" 时用
                  │
                  └── conversation.py        ← 多轮对话管理
```

---

## ReAct Execution Flow

```
┌──────────────────────────┐
│  model.acquire_model()   │  ← Load 7B model ONCE
└────────┬─────────────────┘
         │
         ▼
╔══════════════════════════════════════════════════════════════╗
║              PHASE 1: ReAct Loop (读取已有数据)               ║
║                                                              ║
║  7B 模型的职责: 只读取已有数据 + 分析，不生成原始数据        ║
║                                                              ║
║  Step N (max 10, 目标 3-5 步):                               ║
║  1. Build prompt (system + tool_descriptions + history)       ║
║  2. LLM generate (non-streaming, reuses held model)          ║
║  3. Parse action (regex: "Action: xxx\nAction Input: yyy")   ║
║     ├── Success → execute tool                               ║
║     └── Parse fail → break to Phase 2                        ║
║  4. Execute tool (ToolRegistry)                              ║
║     ├── "generate_final_report" → break                      ║
║     ├── READ → return existing file data                     ║
║     ├── SKILL → LLM analysis on collected data               ║
║     └── MEMORY → save/retrieve cross-session knowledge       ║
║  5. Store observation, append to history, loop               ║
║                                                              ║
║  效率优化 (Prompt 引导 agent 减少无用步骤):                  ║
║  ├── 首次报告: metadata → 批量收集 → generate (3-5步)       ║
║  ├── 追问已有报告: metadata → get_class_report → generate    ║
║  ├── 具体问题: metadata → 1-2个工具 → generate              ║
║  └── 重新生成: 用户明确要求时走完整收集流程                  ║
║                                                              ║
╚════════════════════════════╤═════════════════════════════════╝
                             │
                             ▼
┌────────────────────────────────────────────────────────────┐
│  No Data Check:                                            │
│                                                            │
│  observations == [] → 返回 "无课堂记录数据" → 结束         │
│  observations有数据 → 继续 Phase 2                         │
└────────────────────────────┬───────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────┐
│  Output Format (由 OpenClaw 决定, 传入 output_format):      │
│                                                            │
│  "report" → _build_report_prompt() → 结构化报告            │
│  "chat"   → _build_chat_prompt()   → 简短对话式回答        │
│  None     → _is_report_request()   → 关键词 fallback       │
│             (仅当 output_format 未传时才触发)              │
└────────────────────────────┬───────────────────────────────┘
                             │
                             ▼
╔══════════════════════════════════════════════════════════════╗
║          PHASE 2: Response Generation (Streaming)            ║
║                                                              ║
║  LLM streaming → yield tokens → SSE → OpenClaw → 用户      ║
║                → save to class_report.md                     ║
╚════════════════════════════╤═════════════════════════════════╝
                             │
                             ▼
┌────────────────────────────────────────────────────────────┐
│  model.release_model()   ← Free 7B from GPU memory         │
│  Save trajectory + metrics                                  │
└────────────────────────────────────────────────────────────┘
```

### 典型场景步骤对比

| 场景 | ReAct 步骤 | 说明 |
|------|-----------|------|
| 首次生成完整报告 | 5步 | metadata → statistics → summary → mindmap → topics → generate |
| 追问已有报告 ("参与度如何") | 3步 | metadata → get_class_report → generate |
| 具体问题 ("出几道题") | 3步 | metadata → get_class_summary → generate |
| 重新生成报告 | 5步 | metadata → 重新收集所有数据 → generate |
| 无数据 | 1步 | metadata → 无文件 → 返回提示 |

---

## Model Lifecycle (共享 7B 模型)

```
同一个 Qwen2.5-7B 模型实例被两个场景共享:

场景 A: OpenClaw 路由 (通过 /v1/chat/completions)
  acquire → generate(~200 tokens, 路由判断) → release
  耗时: ~2-3s

场景 B: ReportAgent 分析 (通过 pipeline 内部调用)
  acquire → Step1~N think + Report stream → release
  耗时: ~30-60s

时间线 (天然串行，不冲突):
  ┌──────────┐         ┌──────────────────────────────────────┐
  │ OpenClaw │         │           ReportAgent                 │
  │  路由    │         │  ReAct loop + report generation       │
  │ acquire  │         │  acquire                              │
  │ generate │────────→│  think → think → ... → stream         │
  │ release  │         │  release                              │
  └──────────┘         └──────────────────────────────────────┘
       2-3s                         30-60s

  OpenClaw 先完成路由并释放模型，然后 HTTP 请求到达 /agent/chat,
  ReportAgent 才开始 acquire。两者不会同时持有模型。

保护机制:
  - audio_pipeline_lock 防止并发访问
  - /v1/chat/completions 检测 lock 状态，busy 时返回 429
```

---

## 调用入口: OpenClaw vs Frontend vs Intent Router

```
┌─────────────────────────────────────────────────────────────┐
│  入口 A: OpenClaw (命令行/语音对话)                          │
│                                                             │
│  用户 → OpenClaw → POST /agent/chat                         │
│  {message: "分析参与度", output_format: "chat"}             │
│  session_id 不传 → 自动用最新活跃 session                   │
│  OpenClaw 负责意图识别 + output_format 决策                  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  入口 B: Frontend + Intent Router (无 OpenClaw 时)          │
│                                                             │
│  用户 → UI → POST /chat (统一入口)                          │
│  {message: "生成课堂报告"}                                  │
│  Intent Router 自动判断:                                     │
│    → agent = "report", output_format = "report"             │
│    → 内部调用 /agent/chat                                    │
│                                                             │
│  配置 (config.yaml):                                        │
│    router:                                                  │
│      enabled: true                                          │
│      mode: keyword  # keyword (快, 无LLM) | llm (准, 用7B) │
│                                                             │
│  router.enabled = false 时, /chat 等同于 /agent/chat        │
│  (前端需自行传 output_format)                                │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  入口 C: Frontend 直接调用 (跳过 Router)                    │
│                                                             │
│  前端明确知道要什么 → POST /agent/chat                       │
│  {session_id: "20260526-143000-a1b2", message: "...",       │
│   output_format: "report"}                                  │
│  适用场景: UI 按钮明确对应功能 (如"生成报告"按钮)           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Intent Router 架构

```
POST /chat {message: "..."}
      │
      ▼
┌─────────────────────────────────┐
│  router.enabled == true?         │
│  ├── Yes → IntentRouter.route()  │
│  │   ├── mode=keyword → 正则匹配 (0ms)
│  │   └── mode=llm → 7B 分类 (~2s)
│  │   Result: {agent, output_format, confidence}
│  │   │
│  │   ├── agent="report" → /agent/chat
│  │   ├── agent="homework" → 501 (future)
│  │   └── agent="lesson_prep" → 501 (future)
│  │
│  └── No → 直接调用 /agent/chat (需前端传 output_format)
└─────────────────────────────────┘
```

### 后续扩展路径

当新 Agent 上线时:
1. 在 `components/intent_router.py` 中添加新 agent 的关键词模式
2. 实现新 agent 的 endpoint (如 `/homework/chat`)
3. 在 `/chat` 中添加路由分支
4. 如果部署了 OpenClaw，添加对应 SKILL.md 即可自动路由

---

## API Endpoints

| Endpoint | Method | Description | Called By |
|----------|--------|-------------|-----------|
| `/chat` | POST | **统一入口** (含 Intent Router) | Frontend UI |
| `/v1/chat/completions` | POST | OpenAI 兼容 LLM 接口 | OpenClaw (本地 Provider) |
| `/agent/chat` | POST | 学情Agent 多轮对话 (直接调用) | OpenClaw / `/chat` router |
| `/generate-report` | POST | 单次报告生成 | OpenClaw / Frontend |
| `/report/{session_id}` | GET | 获取已保存报告 | Frontend |

### `/agent/chat` Request/Response

**Request (from OpenClaw, session_id 可选):**
```json
{
  "message": "分析一下今天学生的参与度",
  "output_format": "chat"
}
```

**Request (from Frontend, 指定 session):**
```json
{
  "session_id": "20260526-143000-a1b2",
  "message": "分析一下今天学生的参与度",
  "output_format": "chat",
  "conversation_id": null
}
```

**session_id 解析逻辑:**
- 传了 → 使用指定 session
- 没传 → 从 SessionState 取最新活跃 session
- 无活跃 session → 返回 404 "No session found"

**Response (streaming JSON lines):**
```json
{"token": "根据视频分析数据...", "error": "", "conversation_id": "conv_xxx"}
{"token": "学生平均参与度为...", "error": "", "conversation_id": "conv_xxx"}
```

---

## OpenClaw Setup Guide

### Prerequisites

- Node.js 22.19+ 或 Node 24
- 至少一个 LLM Provider (云端 API key 或 Smart Classroom 本地 7B)
- Smart Classroom 服务已启动 (`localhost:8000`，同时提供 `/v1/chat/completions`)

### Step 1: 安装 OpenClaw

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### Step 2: 配置 Provider 和 Agent

编辑 `~/.openclaw/.env`:
```bash
OPENAI_API_KEY=sk-...   # 云端时用 (本地时可不配)
```

编辑 `~/.openclaw/openclaw.json`:
```json
{
  "gateway": {
    "mode": "local",
    "bind": "loopback",
    "auth": {
      "mode": "token",
      "token": "your-secret-token"
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "openai": {},
      "smart-classroom": {
        "baseUrl": "http://127.0.0.1:8000",
        "apiKey": "local",
        "api": "openai-completions",
        "models": []
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "openai/gpt-4o",
        "fallbacks": ["smart-classroom/Qwen2.5-7B-Instruct"]
      },
      "skipBootstrap": true
    },
    "list": [
      {
        "id": "classroom",
        "default": true,
        "workspace": "~/.openclaw/workspace-classroom",
        "model": {
          "primary": "openai/gpt-4o",
          "fallbacks": ["smart-classroom/Qwen2.5-7B-Instruct"]
        },
        "identity": {
          "name": "学情助手",
          "theme": "classroom analysis assistant",
          "emoji": "📊"
        },
        "tools": {
          "allow": ["web_fetch", "session_status"]
        }
      }
    ]
  }
}
```

> **注意**: 本地 fallback 直接使用 Smart Classroom 服务 (`localhost:8000/v1/chat/completions`)，
> 复用已加载的 Qwen2.5-7B-Instruct 模型，无需额外模型服务。

### Step 3: 添加 Skills

```bash
mkdir -p ~/.openclaw/workspace-classroom/skills
cp -r /path/to/smart-classroom/openclaw-skills/* ~/.openclaw/workspace-classroom/skills/
```

目录结构:
```
~/.openclaw/
├── .env                           ← API keys
├── openclaw.json                  ← Agent + Provider 配置
└── workspace-classroom/
    └── skills/
        ├── classroom-report/SKILL.md      ← 学情Agent
        ├── classroom-homework/SKILL.md    ← 作业Agent (future)
        └── classroom-lesson-prep/SKILL.md ← 备课Agent (future)
```

### Step 4: 启动

```bash
# 启动 OpenClaw
openclaw gateway start
openclaw gateway status
openclaw skills list  # 验证 skills 加载

# 启动 Smart Classroom
cd /path/to/smart-classroom
python main.py  # localhost:8000
```

### Step 5: 验证

```bash
# 健康检查
curl http://localhost:8000/health

# 通过 OpenClaw 对话测试
openclaw chat "帮我分析今天课堂的学生参与度"
```

---

## 概念对照

| 概念 | 作用 | 位置 |
|------|------|------|
| **Provider** | LLM 来源 (OpenAI / Smart Classroom 本地) | OpenClaw `openclaw.json` |
| **Agent** | 助手实例 (workspace + model) | OpenClaw `openclaw.json` |
| **Skill (SKILL.md)** | 教 Agent 调哪个 API、传什么参数 | `~/.openclaw/workspace-classroom/skills/` |
| **Report Agent** | 学情分析执行器 (ReAct + 7B) | Smart Classroom Python code |
| **output_format** | 输出格式 hint (report/chat) | OpenClaw → Smart Classroom API param |
| **/v1/chat/completions** | 本地 LLM 服务 (OpenAI 兼容) | Smart Classroom `api/llm_serving.py` |

---

## File Structure

```
smart-classroom/
├── main.py                        ← FastAPI 启动入口 (port 8000)
├── api/
│   ├── endpoints.py               ← HTTP endpoints (called by OpenClaw / Frontend)
│   └── llm_serving.py             ← OpenAI-compatible /v1/chat/completions (本地 Provider)
├── pipeline.py                    ← Pipeline factory
├── config.yaml                    ← App configuration (含 router 开关)
│
├── components/intent_router.py    ← Intent Router (keyword/llm 模式, 替代 OpenClaw 路由)
│
├── components/report_agent/       ← 学情Agent (implemented)
│   ├── report_agent.py            ← ReAct loop + streaming generation
│   ├── tools.py                   ← 16 tools (READ/MEMORY/SKILL/CONTROL) + AgentMemory
│   ├── prompts.py                 ← All prompt templates (效率优化: 引导 agent 3-5步完成)
│   ├── conversation.py            ← Multi-turn state manager
│   └── skills/                    ← 6 analysis skills
│
├── ui/                            ← Frontend (React + TypeScript + Vite)
│   ├── src/components/RightPanel/
│   │   └── AgentChatAccordion.tsx ← 学情Agent 对话界面 (调 POST /chat)
│   ├── src/services/api.ts        ← streamAgentChat() → POST /chat (经 Intent Router)
│   ├── src/i18n/{en,zh}.json      ← 国际化 (agent.* 键)
│   └── dist/                      ← 构建产物, 由 FastAPI StaticFiles 提供
│
├── openclaw-skills/               ← SKILL.md files for OpenClaw
│   ├── classroom-report/SKILL.md
│   ├── classroom-homework/SKILL.md
│   └── classroom-lesson-prep/SKILL.md
│
└── docs/
    └── report-agent-architecture.md  ← This document
```

---

## Frontend UI — 学情Agent 对话界面

```
┌─────────────────────────────────────────────┐
│  学情Agent                                   │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │ (对话流, 支持 markdown 渲染)            │  │
│  │                                        │  │
│  │ 推荐问题 (首次进入时显示):              │  │
│  │ ┌────────────────────────────────────┐ │  │
│  │ │ 今天学生表现怎么样？                │ │  │
│  │ ├────────────────────────────────────┤ │  │
│  │ │ 哪个时间段参与度最低？              │ │  │
│  │ ├────────────────────────────────────┤ │  │
│  │ │ 帮我生成一份完整的课堂评估报告      │ │  │
│  │ ├────────────────────────────────────┤ │  │
│  │ │ 根据今天课程内容出5道测验题         │ │  │
│  │ └────────────────────────────────────┘ │  │
│  │                                        │  │
│  │ (点击推荐问题 → 直接发送, 非填入输入框) │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  [+] [输入框: 输入问题...]      [发送/停止]  │
│                                              │
└─────────────────────────────────────────────┘

[+] = 开始新对话 (清空历史, 重置 conversation_id)
推荐问题 = 点击后直接发送, agent 自主决定收集哪些数据
```

### 前端交互流程

```
用户进入页面 → 看到推荐问题列表
  │
  ├── 点击推荐问题 或 自由输入
  │
  ▼
POST /chat {message: "今天学生表现怎么样？", session_id: "..."}
  │
  ├── Intent Router (keyword): agent=report, output_format=chat
  │
  ▼
/agent/chat → ReportAgent ReAct loop → 流式返回
  │
  ▼
前端实时渲染 markdown (表格、列表、标题)
  │
  ├── 用户追问 (自动携带 conversation_id)
  │   POST /chat {message: "再详细看看", conversation_id: "conv_xxx"}
  │   → agent 读 class_report.md → 直接回答 (3步)
  │
  └── 用户点 [+] → 清空对话, 开始新话题
```

### 设计原则

- **以对话为主入口**, 不设固定功能按钮 — 体现 agent 的自主性
- 推荐问题是引导, 不是限制 — 用户可以问任何课堂相关问题
- 没有"生成报告"按钮, 用户通过自然语言表达需求, router 自动判断 output_format
- 追问不需要重新收集数据 — agent 读已有报告回答

---

## Adding a New Agent

To add a new agent (e.g., homework_agent):

1. **Smart Classroom (后端):**
   - Create `components/homework_agent/` with agent logic
   - Add API endpoint: `POST /homework/chat`
   - Expose via `pipeline.py`

2. **Intent Router (本地路由):**
   - 在 `components/intent_router.py` 中添加 `_HOMEWORK_PATTERNS` 关键词
   - 在 `api/endpoints.py` 的 `/chat` endpoint 中添加路由分支

3. **Frontend (UI):**
   - 在推荐问题中添加作业相关提示
   - 或: 新增独立的作业 Agent 对话面板

4. **OpenClaw (可选, 有网络时):**
   - `openclaw-skills/classroom-homework/SKILL.md` already exists
   - Update it: change status from "Under Development" to active
   - Add the correct API call (`POST /homework/chat`)
   - OpenClaw 部署后自动路由, 无需改代码
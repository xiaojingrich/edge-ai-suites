# Smart Classroom Report Agent — Architecture Document

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
│  ⚠️  只有用户问题经过 OpenClaw，原始课堂数据不出设备                           │
│                                                                                 │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │ HTTP (localhost)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Smart Classroom (执行层, 不做路由)                             │
│                                                                                 │
│  API Endpoints:                                                                 │
│  ├── POST /chat              ← 统一入口 (含 Intent Router)                      │
│  ├── POST /agent/chat        ← 学情Agent (多轮对话, 支持 output_format)         │
│  ├── POST /generate-report   ← 学情Agent (单次报告)                             │
│  ├── GET  /report/{id}       ← 获取已生成的报告                                │
│  ├── GET  /report/{id}/download ← 下载 Word 格式报告                           │
│  ├── POST /report/template/upload ← 上传自定义报告模板                          │
│  ├── POST /homework/...      ← 作业Agent (future)                              │
│  └── POST /lesson-prep/...   ← 备课Agent (future)                              │
│                                                                                 │
│  Report Agent:                                                                  │
│  ├── Local 7B LLM (Qwen2.5-7B-Instruct, OpenVINO)                             │
│  ├── 只读取已有数据 (ReAct) + 分析推理 + 文本生成                               │
│  ├── 工具自动计算统计指标 (语速、提问次数、密度等)                               │
│  ├── 支持 Word 模板报告生成 (.docx)                                             │
│  ├── 不生成原始数据 (ASR/Summary/Mindmap 由主 pipeline 完成)                    │
│  └── 无数据时返回提示，不报错                                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Report Agent 双路径架构

Report Agent 有两条执行路径：**Fast Path**（快速路径）和 **ReAct Loop**（推理循环）。

```
┌────────────────────────────────────────────────────────────────┐
│                     generate_report() 入口                       │
│                                                                  │
│  model.acquire_model()                                           │
│        │                                                         │
│        ▼                                                         │
│  ┌─────────────────────────────────────┐                        │
│  │  _can_use_fast_path()?              │                        │
│  │                                     │                        │
│  │  YES if:                            │                        │
│  │  - 首次报告请求 (_is_report_request)│                        │
│  │  - 已有报告 + 追问                  │                        │
│  └───────┬─────────────────┬───────────┘                        │
│      YES │                 │ NO                                  │
│          ▼                 ▼                                     │
│  ┌─────────────────┐  ┌─────────────────┐                      │
│  │  Fast Path      │  │  ReAct Loop     │                      │
│  │  0 LLM calls    │  │  LLM-guided     │                      │
│  │  直接读全部数据  │  │  自主决定工具    │                      │
│  └────────┬────────┘  └────────┬────────┘                      │
│           │                    │                                 │
│           └────────┬───────────┘                                │
│                    ▼                                             │
│  ┌─────────────────────────────────────┐                        │
│  │  Phase 2: 生成阶段                  │                        │
│  │                                     │                        │
│  │  有模板 → LLM 输出 JSON → 填充 .docx│                        │
│  │  无模板 → LLM 流式输出 Markdown      │                        │
│  └─────────────────────────────────────┘                        │
│                    │                                             │
│  model.release_model()                                           │
└────────────────────────────────────────────────────────────────┘
```

### Fast Path（快速路径 — 0 LLM 推理调用）

适用条件：
- 用户请求完整报告（关键词匹配）
- 已有 `class_report.md` 且用户在追问

执行过程：
1. `intent_analysis` — 直接判定意图（无 LLM）
2. 依次调用所有 READ 工具收集数据
3. 进入生成阶段

**优势**：数据收集阶段无 LLM 调用，仅在最终生成时使用一次 LLM。

### ReAct Loop（推理循环 — LLM 引导）

适用条件：
- 具体问题（"参与度如何"、"出几道测验题"）
- 需要 LLM 判断该收集哪些数据

执行过程：
1. `intent_analysis` — LLM 分析意图并规划
2. LLM 逐步决定调用哪些工具（支持批量调用）
3. 收集到足够数据后调用 `generate_final_report`
4. 进入生成阶段

最多 10 步，目标 3-5 步完成。

---

## 工具列表 (18 Tools)

### READ 工具（只读取已有数据，无副作用）

| # | 工具名 | 说明 | 计算统计 |
|---|--------|------|----------|
| 1 | `get_session_metadata` | 检查 session 状态：可用文件、时长 | — |
| 2 | `get_class_report` | 读取已生成的报告 (`class_report.md`) | — |
| 3 | `get_class_statistics` | 读取学生参与统计 (`va/class_statistics.json`) | — |
| 4 | `get_class_summary` | 读取课堂摘要 (`summary.md`) | — |
| 5 | `get_mindmap` | 读取思维导图 (`mindmap.mmd`) | — |
| 6 | `get_topic_segmentation` | 读取主题分段 (`topics.json`) | — |
| 7 | `get_transcription` | 读取原始转录 (`transcription.txt`) | — |
| 8 | `get_teacher_transcription` | 读取教师转录 (`teacher_transcription.txt`) | ✅ 计算语速、提问次数 |
| 9 | `get_content_segmentation` | 读取内容分段转录 (`content_segmentation_transcription.txt`) | ✅ 计算密度、低活跃时段 |
| 10 | `get_memory` | 读取跨 session 历史记忆 | — |

### MEMORY 工具

| # | 工具名 | 说明 |
|---|--------|------|
| 11 | `save_memory` | 保存关键发现到持久化记忆 |

### SKILL 工具（LLM + 数据组合分析）

| # | 工具名 | 说明 |
|---|--------|------|
| 12 | `skill_engagement_analysis` | 参与度评分 + 模式分析 |
| 13 | `skill_video_slice_summary` | 识别关键教学片段 |
| 14 | `skill_content_analysis` | 教学目标与知识覆盖分析 |
| 15 | `skill_ocr_board_analysis` | 板书/PPT 内容分析 |
| 16 | `skill_quiz_generation` | 根据课堂内容生成 5 道测验题 |
| 17 | `skill_teacher_behavior` | 教师行为与教学风格分析 |

### CONTROL 工具

| # | 工具名 | 说明 |
|---|--------|------|
| 18 | `generate_final_report` | 结束数据收集，进入生成阶段 |

### 工具内计算（非 LLM）

`get_teacher_transcription` 自动计算：
- `total_sentences` — 总句数
- `total_chars` — 总字符数
- `question_count` — 提问次数（以"？"结尾的句子）
- `audio_duration` — 音频时长
- `speaking_speed` — 语速（字符/分钟）

`get_content_segmentation` 自动计算：
- `total_segments` — 总段数
- `total_duration` — 总时长
- `avg_segment_duration` — 平均段长
- `density per 5-min` — 每 5 分钟的内容密度
- `low_activity_periods` — 低活跃时段

这些统计由工具代码直接计算，不消耗 LLM 调用。LLM 仅做定性分析和文本生成。

---

## Word 模板报告系统

### 模板优先级

```
1. Session 自定义: {session_dir}/custom_report_template.docx
2. Project 自定义: {project_dir}/report_template.docx
3. 默认模板:       templates/report_template_{language}.docx
```

### 模板格式

```
.docx 文件中：
- Heading 定义章节结构
- {placeholder_name} 标记 LLM 需要填充的字段
- 无占位符的文字保持不变
```

### 模板模式执行流程

```
[工具收集数据] → [提取模板结构] → [LLM 分析数据填充字段] → [填充模板] → [保存 .docx]
                        │                    │                      │
            extract_template_structure    LLM 看到数据+模板         fill_template
            返回 sections + all_fields    输出 "field: value" 格式   替换占位符
```

设计原则：**工具只负责收集数据，模型自行决定如何填充模板**。

1. `extract_template_structure()` — 解析 .docx，提取 sections + placeholder 字段列表
2. 构建 prompt — 将收集到的课堂数据 + 模板结构（字段列表和原文）一起提供给 LLM
3. LLM 分析数据并输出 — 格式为 `field_name: 填充内容`（每行一个字段，比 JSON 更容错）
4. `_parse_template_fill_response()` — 解析 key-value 格式响应
5. `fill_template()` — 将字段值替换到 .docx 模板中，保留原有格式

**灵活性**：模板字段变化时不需要修改代码，模型会根据提供的数据自行判断如何填充。

### 无模板模式

LLM 直接流式输出 Markdown → 保存为 `class_report.md` → 下载时转为 .docx

### API 接口

| Endpoint | 说明 |
|----------|------|
| `POST /report/template/upload` | 上传自定义 .docx 模板（替换项目级默认） |
| `GET /report/{session_id}/download` | 下载报告，优先返回模板生成的 .docx，否则 md→docx 转换 |

---

## 前端 UI — 浮动对话框

### UI 布局

```
┌────────────────────────────────────────────────────────────────────┐
│  Header Bar                                                         │
│  [Record] [Upload]        通知区域        [🤖 Agent] [项目名]       │
└────────────────────────────────────────────────────────────────────┘

点击 [🤖 Agent] → 弹出浮动对话框:

┌────────────────────────────────────────┐
│  Class Report Agent          [+] [×]   │
├────────────────────────────────────────┤
│                                        │
│  推荐问题:                             │
│  ┌──────────────────────────────────┐  │
│  │ 今天学生表现怎么样？              │  │
│  │ 哪个时间段参与度最低？            │  │
│  │ 帮我生成一份完整的课堂评估报告    │  │
│  │ 根据今天课程内容出5道测验题       │  │
│  └──────────────────────────────────┘  │
│                                        │
│  (对话流区域 — Plan 进度 + Markdown)    │
│                                        │
│  ┌─ Plan Progress ───────────────────┐ │
│  │ ● 2/6 steps                       │ │
│  │ ✓ intent_analysis                 │ │
│  │ ✓ get_class_statistics            │ │
│  │ ● get_class_summary        [LLM]  │ │
│  │ ○ get_mindmap                     │ │
│  │ ○ get_topic_segmentation          │ │
│  │ ○ generate               [LLM]    │ │
│  └────────────────────────────────────┘ │
│                                        │
│  (Markdown 渲染的回复内容)              │
│  [Download Word Report]                 │
│                                        │
├────────────────────────────────────────┤
│  [输入框]                    [发送]     │
└────────────────────────────────────────┘
```

### Plan 进度显示

Agent 执行时实时展示完整计划和进度：

| 状态 | 图标 | 含义 |
|------|------|------|
| pending | ○ | 待执行 |
| running | ● (动画) | 正在执行 |
| done | ✓ | 已完成 |
| [LLM] | 橙色徽章 | 该步骤使用大模型 |

事件流：
```
plan        → 显示完整步骤列表（所有步骤初始为 pending）
plan_update → 动态追加步骤（ReAct 模式，发现新需求时）
step_start  → 将指定步骤标为 running
step_done   → 将指定步骤标为 done
```

### 前端组件结构

```
Header.tsx
  └── [🤖 Agent] button (agent-nav-btn)
      └── AgentChatDialog.tsx (浮动对话框)
          ├── PlanBlock (计划进度组件)
          ├── ReactMarkdown (回复渲染)
          └── Download button (report_ready 时显示)
```

---

## SSE 事件流协议

### 事件类型

| 后端 type | 前端映射 | 说明 |
|-----------|----------|------|
| `plan` | `agent_plan` | 完整计划步骤列表 |
| `plan_update` | `agent_plan_update` | 动态更新步骤（新增步骤） |
| `step_start` | `agent_step_start` | 步骤开始执行 |
| `step_done` | `agent_step_done` | 步骤执行完成 |
| `token` | `agent_token` | 生成的文本 token |
| `report_ready` | `report_ready` | 报告生成完毕，可下载 |
| `thinking` | `agent_thinking` | 旧版思考事件（兼容） |

### 数据格式 (JSON Lines)

```json
{"type": "plan", "steps": [{"action": "intent_analysis", "thought": "理解意图", "llm": false}, ...], "conversation_id": "conv_xxx"}
{"type": "step_start", "index": 0, "conversation_id": "conv_xxx"}
{"type": "step_done", "index": 0, "conversation_id": "conv_xxx"}
{"type": "step_start", "index": 1, "conversation_id": "conv_xxx"}
{"type": "step_done", "index": 1, "conversation_id": "conv_xxx"}
...
{"token": "根据课堂数据分析...", "error": "", "conversation_id": "conv_xxx"}
{"type": "report_ready", "session_id": "20260526-xxx", "conversation_id": "conv_xxx"}
```

`index: -1` 表示最后一个步骤（生成阶段）。

---

## 多轮对话管理

### ConversationManager

```
{session_dir}/.conversations/
  └── conv_{timestamp}_{random}.json
      {
        "conversation_id": "conv_xxx",
        "session_id": "...",
        "messages": [
          {"role": "user", "content": "...", "timestamp": "..."},
          {"role": "assistant", "content": "...", "timestamp": "..."}
        ],
        "agent_observations": [
          "[get_class_statistics] ...",
          "[get_class_summary] ..."
        ]
      }
```

追问时：
- 前端携带 `conversation_id`
- 后端加载之前的 `agent_observations` 作为 `prior_observations`
- Agent 不需重新收集数据（走 Fast Path: 读 `class_report.md`）

---

## Model Lifecycle

```
同一个 Qwen2.5-7B 模型实例，acquire/release 模式管理 GPU 内存：

Pipeline 构造时不加载模型，只初始化 tokenizer。
Agent 执行时:
  acquire_model()  → 加载到 GPU
  ReAct loop (0~10 次 LLM 调用)
  Report generation (1 次 LLM 调用)
  release_model()  → 从 GPU 卸载

保护机制:
  - audio_pipeline_lock: 防止与 ASR/Summary 并发访问 GPU
  - Agent 启动前检测 lock 状态，如被占用返回 "请等待..."
  - acquire/release 确保整个 Agent 执行期间模型不被反复加载/卸载
    (避免 GPU 内存碎片化导致 "probability tensor contains inf/nan" 错误)
```

---

## Data Privacy Boundary

```
┌─────────────────────────────────────────────────────────────┐
│              What Goes Through OpenClaw (可出设备)            │
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
│  🔒 Word 报告文件 (.docx)                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 典型执行场景

### 场景 1: 首次生成完整报告（Fast Path + 模板）

```
用户: "帮我生成一份完整的课堂评估报告"

→ Fast Path (_is_report_request = true)
→ Plan:
  1. intent_analysis (无 LLM)
  2. get_class_statistics
  3. get_class_summary
  4. get_mindmap
  5. get_topic_segmentation
  6. get_teacher_transcription  ← 自动计算语速、提问次数
  7. get_content_segmentation  ← 自动计算密度、低活跃时段
  8. generate [LLM]            ← LLM 生成 JSON
  9. fill_template             ← 填充 Word 模板

→ 输出: class_report.docx + class_report.md
→ 前端显示 Markdown + [Download Word Report] 按钮
```

**LLM 调用次数**: 1 次（仅生成阶段）

### 场景 2: 追问已有报告（Fast Path）

```
用户: "参与度最低的时间段是哪个？"

→ Fast Path (class_report.md 存在)
→ Plan:
  1. intent_analysis (无 LLM)
  2. get_class_report  ← 读已有报告
  3. generate [LLM]    ← 基于报告回答问题

→ 输出: 对话式简短回答
```

**LLM 调用次数**: 1 次

### 场景 3: 具体问题（ReAct Loop）

```
用户: "根据今天课程内容出5道测验题"

→ ReAct Loop (非报告请求，无已有报告覆盖此需求)
→ Plan (动态):
  1. intent_analysis [LLM]     ← LLM 分析意图
  2. get_class_summary          ← LLM 决定需要摘要
  3. generate [LLM]            ← 生成测验题

→ 输出: 5 道选择题 (Markdown 格式)
```

**LLM 调用次数**: 2 次（意图分析 + 生成）

### 场景 4: 无数据

```
用户: "分析学生参与度"

→ 所有 READ 工具返回 "NOT available"
→ observations == []
→ 直接返回: "当前无课堂记录数据，请先完成一节课的录制。"

→ 无 LLM 调用
```

---

## 文件结构

```
smart-classroom/
├── main.py                              ← FastAPI 启动入口 (port 8000)
├── api/
│   ├── endpoints.py                     ← HTTP endpoints
│   └── llm_serving.py                   ← OpenAI-compatible /v1/chat/completions
├── pipeline.py                          ← Pipeline factory (含 run_report)
├── config.yaml                          ← 应用配置
│
├── components/
│   ├── intent_router.py                 ← Intent Router (keyword/llm 模式)
│   └── report_agent/                    ← 学情Agent
│       ├── report_agent.py              ← ReAct loop + Fast Path + 流式生成
│       ├── tools.py                     ← 18 工具 + 统计计算 + AgentMemory
│       ├── prompts.py                   ← Prompt 模板 (ReAct/Report/Chat)
│       ├── conversation.py              ← 多轮对话状态管理
│       └── skills/                      ← 6 个分析技能
│           ├── __init__.py              ← SKILL_REGISTRY
│           ├── base_skill.py            ← BaseSkill ABC
│           ├── engagement_analysis.py   ← 参与度分析
│           ├── content_analysis.py      ← 内容分析
│           ├── quiz_generation.py       ← 测验题生成
│           ├── teacher_behavior.py      ← 教师行为分析
│           ├── video_slice_summary.py   ← 关键片段识别
│           └── ocr_board_analysis.py    ← 板书/PPT 分析
│
├── utils/
│   ├── template_manager.py              ← Word 模板管理 (解析/填充)
│   └── docx_export.py                   ← Markdown → Word 转换 (降级方案)
│
├── templates/
│   ├── report_template_zh.docx          ← 默认中文模板 (27 个占位字段)
│   └── report_template_en.docx          ← 默认英文模板
│
├── ui/src/
│   ├── components/
│   │   ├── Header/Header.tsx            ← 含 🤖 Agent 按钮
│   │   └── AgentChatDialog.tsx          ← 浮动对话框 (PlanBlock + Chat)
│   ├── services/api.ts                  ← streamAgentChat() + PlanStep 接口
│   ├── assets/css/AgentChat.css         ← Agent UI 样式
│   └── i18n/{en,zh}.json               ← 国际化 (agent.* 键)
│
├── dto/report_dto.py                    ← ReportRequest / AgentChatRequest DTO
│
└── docs/
    └── report-agent-architecture.md     ← 本文档
```

---

## API Endpoints

| Endpoint | Method | 说明 | 调用方 |
|----------|--------|------|--------|
| `/chat` | POST | **统一入口** (含 Intent Router) | Frontend UI |
| `/agent/chat` | POST | 学情Agent 多轮对话 | OpenClaw / Router |
| `/generate-report` | POST | 单次报告生成 | Frontend / OpenClaw |
| `/report/{session_id}` | GET | 获取已保存报告 (Markdown) | Frontend |
| `/report/{session_id}/download` | GET | 下载 Word 报告 | Frontend |
| `/report/template/upload` | POST | 上传自定义 .docx 模板 | Frontend |
| `/v1/chat/completions` | POST | OpenAI 兼容 LLM 接口 | OpenClaw (本地 Provider) |

### `/agent/chat` Request

```json
{
  "session_id": "20260526-143000-a1b2",     // 可选，不传则用最新 session
  "message": "分析一下今天学生的参与度",
  "output_format": "chat",                   // "report" | "chat" | null
  "conversation_id": "conv_xxx"              // 可选，追问时携带
}
```

### `/agent/chat` Response (Streaming JSON Lines)

```json
{"type": "plan", "steps": [...], "conversation_id": "conv_xxx"}
{"type": "step_start", "index": 0, "conversation_id": "conv_xxx"}
{"type": "step_done", "index": 0, "conversation_id": "conv_xxx"}
{"token": "根据课堂数据...", "error": "", "conversation_id": "conv_xxx"}
{"type": "report_ready", "session_id": "xxx", "conversation_id": "conv_xxx"}
```

---

## Intent Router（意图分析层）

意图分析层是所有用户请求的第一道关卡。它决定：
1. 是否需要调用 Agent（学情/作业/备课）
2. 还是直接在该层用模型回答（通用问答/闲聊）

**设计原则**：Agent 只处理领域任务，不处理通用对话。无关问题在意图层直接响应，不下发到 Agent。

```
POST /chat {message: "..."}
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  IntentRouter.route(message)                         │
│  ├── mode=keyword → 正则匹配 (0ms)                  │
│  └── mode=llm → 7B 分类 (~2s)                       │
│                                                      │
│  Result: {agent, output_format, confidence}          │
│  │                                                   │
│  ├── agent="report"      → Report Agent (学情)       │
│  ├── agent="homework"    → Homework Agent (作业)     │
│  ├── agent="lesson_prep" → Lesson Prep Agent (备课)  │
│  └── agent="general"     → 意图层直接调用模型回答     │
│                            （不进入任何 Agent）        │
└─────────────────────────────────────────────────────┘
```

### general 路由（意图层直接回答）

当判定为 `general`（闲聊、问候、与课堂无关的问题），意图层直接用 LLM 生成回复：
- 不收集课堂数据
- 不触发 Agent 的 ReAct/Fast Path 流程
- Prompt 简单：system="你是一个课堂助手" + user message
- 流式返回，和 Agent 共用同一个前端 token 渲染

---

## 不做什么（由主 Pipeline 在课中/课后完成）

| 数据 | 谁生成 | 何时生成 |
|------|--------|----------|
| `transcription.txt` | ASR Pipeline | 课堂中 (实时) |
| `teacher_transcription.txt` | ASR Pipeline (Speaker Diarization) | 课堂中 |
| `content_segmentation_transcription.txt` | ASR Pipeline | 课堂中 |
| `summary.md` | Summarizer | 课后 (自动) |
| `mindmap.mmd` | Mindmap Generator | 课后 (自动) |
| `topics.json` | Content Segmentation | 课后 (自动) |
| `va/class_statistics.json` | VA Pipeline 停止时自动保存 | 课后 |
| `va/front_posture.txt` | Video Analytics | 课堂中 (实时) |

Report Agent **只读取**这些已有数据，绝不触发生成。

---

## Skill 技能系统

Skills 是高级分析能力，组合 READ 工具 + LLM 推理：

```python
class BaseSkill(ABC):
    def __init__(self, session_id, tools, model):
        ...

    def execute(self, context=None) -> dict:
        # 返回: {"status": "success"|"partial"|"unavailable",
        #        "result": {...},
        #        "summary": "一行总结"}
        ...

    def _call_llm(self, prompt) -> str:
        # 辅助方法: 调用 LLM 做分析推理
        ...
```

### 示例: EngagementAnalysisSkill

```
1. 调用 get_class_statistics 获取原始数据
2. 计算: interactions_per_student = (raise + stand) / students
3. 判定: High (≥3) / Medium (≥1) / Low (<1)
4. (可选) 调用 LLM 做深度模式分析
5. 返回结构化结果 + 一行摘要
```

---

## 跨 Session 记忆系统

```
{project_dir}/.agent_memory/memory.jsonl

每行一条记录:
{"session_id": "...", "timestamp": "...", "category": "observation", "content": "..."}
```

- `save_memory`: Agent 在生成报告前保存关键发现
- `get_memory`: 需要趋势分析或跨课时对比时调用
- 支持关键词搜索，返回最近 20 条相关记录

---

## Performance Metrics

每次 Agent 执行后自动保存性能数据：

```
{session_dir}/performance_metrics.csv

- performance.report_react_steps   ← ReAct 步数
- performance.report_react_time    ← 数据收集耗时
- performance.report_generation_time ← 报告生成耗时
- performance.report_total_time    ← 总耗时
- performance.report_ttft          ← 首 token 时间
```

同时保存完整推理轨迹：
```
{session_dir}/report_agent_trajectory.json

{
  "session_id": "...",
  "user_query": "...",
  "steps": 5,
  "observations_count": 6,
  "trajectory": ["Step 1: ...", "Step 2: ..."],
  "observations": ["[get_class_statistics] ...", ...]
}
```

---

## Adding a New Agent

1. **后端**: 创建 `components/{new_agent}/` + API endpoint
2. **Intent Router**: 在 `intent_router.py` 添加关键词模式，在 `/chat` 添加路由分支
3. **前端**: 新增对话面板或复用现有 AgentChatDialog
4. **OpenClaw** (可选): 添加 `openclaw-skills/{agent}/SKILL.md`

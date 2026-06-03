# Smart Classroom Report Agent — Architecture Document

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    OpenClaw (可选入口, 云端路由 + 编排)                           │
│                                                                                 │
│  ┌─── Provider (云端) ──────────────┐  ┌─── Provider (本地) ──────────────────┐│
│  │  Cloud LLM (GPT-4o / Claude)     │  │  Smart Classroom /v1/chat/completions ││
│  │  精准意图识别 + Skill 选择       │  │  本地 Qwen 意图识别 + Skill 选择    ││
│  └───────────────────────────────────┘  └────────────────────────────────────┘│
│                                                                                 │
│  Skills (SKILL.md) — 教 LLM 调用哪个 API:                                      │
│  ├── classroom-report     → POST /report/chat                                   │
│  ├── classroom-homework   → POST /homework/chat (future)                        │
│  └── classroom-lesson-prep → POST /lesson-prep/chat (future)                    │
│                                                                                 │
│  ⚠️  只有用户问题经过 OpenClaw，原始课堂数据不出设备                           │
│                                                                                 │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │ HTTP (localhost)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Smart Classroom (执行层 + 本地编排)                            │
│                                                                                 │
│  ┌── Orchestrator (本地编排层, 替代 OpenClaw) ──────────────────────────────┐   │
│  │  POST /chat → Orchestrator.handle_chat()                                │   │
│  │    1. 管理会话 (session + conversation)                                  │   │
│  │    2. IntentRouter 意图分类 (keyword/llm)                                │   │
│  │    3. 分发到 registered handlers:                                        │   │
│  │       ├── "general"     → LLM 直接回答 (带上下文)                        │   │
│  │       ├── "report"      → Report Agent (学情)                            │   │
│  │       ├── "homework"    → Homework Agent (future)                        │   │
│  │       └── "lesson_prep" → Lesson Prep Agent (future)                     │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  API Endpoints:                                                                 │
│  ├── POST /chat              ← 统一入口 → Orchestrator                          │
│  ├── POST /report/chat       ← 学情Agent (OpenClaw / 直接调用)                  │
│  ├── POST /report/generate   ← 学情Agent (单次报告)                             │
│  ├── GET  /report/{id}       ← 获取已生成的报告                                │
│  ├── GET  /report/{id}/download ← 下载 Word 格式报告                           │
│  ├── POST /report/template/upload ← 上传自定义报告模板                          │
│  ├── GET  /conversations/{session_id}          ← 会话列表                       │
│  ├── GET  /conversations/{session_id}/{id}     ← 会话消息                       │
│  ├── DELETE /conversations/{session_id}/{id}   ← 删除会话                       │
│  ├── POST /homework/chat     ← 作业Agent (future)                              │
│  └── POST /lesson-prep/chat  ← 备课Agent (future)                              │
│                                                                                 │
│  Report Agent:                                                                  │
│  ├── Local LLM (Qwen, OpenVINO INT8, 可配置 LLM_MODEL_PATH)                  │
│  ├── 只读取已有数据 (ReAct) + 分析推理 + 文本生成                               │
│  ├── 工具自动计算统计指标 (语速、提问次数、密度等)                               │
│  ├── 支持 Word 模板报告生成 (.docx)                                             │
│  ├── 不生成原始数据 (ASR/Summary/Mindmap 由主 pipeline 完成)                    │
│  └── 无数据时返回提示，不报错                                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Report Agent 架构 — 纯 ReAct Agent

Report Agent 是真正的 Agent：**LLM 自主决定调用哪些工具、何时停止收集、何时生成输出**。

```
┌────────────────────────────────────────────────────────────────┐
│                     generate_report() 入口                       │
│                                                                  │
│  ┌─────────────────────────────────────┐                        │
│  │  ReAct Loop (LLM 自主决策)           │                        │
│  │                                     │                        │
│  │  Step 1: get_session_metadata       │  ← 查看有哪些文件可用   │
│  │  Step 2: LLM 决定调哪些工具         │  ← 根据文件列表选择     │
│  │         (支持批量调用)              │                        │
│  │  Step 3: generate_final_report      │                        │
│  │                                     │                        │
│  │  保护: 无数据时拒绝 generate         │                        │
│  │  最多 6 步                           │                        │
│  └───────────────┬─────────────────────┘                        │
│                  │                                               │
│         observations 为空?                                       │
│         (ReAct 失败/LLM 出错/跳过工具)                           │
│           │YES        │NO                                        │
│           ▼           │                                          │
│  ┌────────────────┐   │                                          │
│  │ Workflow        │   │  ← 确定性按顺序调用所有 READ 工具       │
│  │ Fallback       │   │     保证只要有数据就一定能拿到           │
│  └───────┬────────┘   │                                          │
│          └────────────┘                                          │
│                  │                                               │
│                  ▼                                               │
│  ┌─────────────────────────────────────┐                        │
│  │  Phase 2: 生成阶段                  │                        │
│  │                                     │                        │
│  │  report + 模板 → LLM 填充 → .docx   │                        │
│  │  report + 无模板 → LLM 流式 Markdown │                        │
│  │  chat → LLM 流式回答 (不写文件)      │                        │
│  └─────────────────────────────────────┘                        │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

### ReAct Loop（推理循环）

所有请求统一走 ReAct Loop，LLM 自主决策：

**完整报告请求**（"生成完整报告"）：
```
Step 1: get_session_metadata → 了解可用文件
Step 2: Actions (批量):
        - get_class_statistics
        - get_class_summary
        - get_mindmap
        - get_topic_segmentation
        - get_teacher_transcription
        - get_content_segmentation
Step 3: generate_final_report
```
LLM 调用 2 次（规划 + 生成），工具调用 1 步完成。

**具体问题**（"哪个时段参与度最低"）：
```
Step 1: get_session_metadata
Step 2: Actions (批量):
        - get_class_statistics
        - get_content_segmentation
Step 3: generate_final_report
```
LLM 只收集相关数据，不过度收集。

**追问已有报告**：
```
Step 1: get_session_metadata → 看到 class_report.md 存在
Step 2: get_class_report → 读取已有报告
Step 3: generate_final_report（或补充调用其他工具）
```
LLM 自行判断是否需要原始数据。

### 健壮性保护

- **最多 6 步**：防止推理发散
- **模糊工具名匹配**：模型可能 typo，自动纠正到最近的工具名
- **空数据拦截**：模型在没有调任何数据工具时调 generate_final_report，代码层忽略并继续循环
- **Workflow Fallback**：ReAct 结束后如果 observations 仍为空，自动降级为确定性 workflow（按顺序调所有 READ 工具）
- **LLM 错误容错**：推理步骤中 LLM 失败（如 sampling NaN/Inf）则终止收集，触发 fallback
- **不可用数据自动跳过**：工具返回 "NOT available" 时不计入 observations
- **Chat 模式保护**：chat 问答不覆盖已生成的 `class_report.md`，只有 report 模式才写入报告文件

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
[工具收集数据] → [读取模板原文] → [LLM 输出填充后完整文本] → [保存]
                       │                    │                    │
           read_template_text()       LLM 看到模板+数据      保存为 .md
           读取 .docx 全文            自行判断哪些是占位符    下载时转 .docx
                                      输出完整填好的文本
```

设计原则：**模板不需要特殊占位符格式，LLM 自行判断哪些内容需要替换**。

1. `read_template_text()` — 读取 .docx 模板的全部文本内容
2. 构建 prompt — 将模板原文 + 收集到的课堂数据一起提供给 LLM
3. LLM 识别模板中的占位内容（如 XXX、XX 等）并用实际数据替换
4. LLM 流式输出填写完成的完整报告文本
5. 保存为 `class_report.md`，下载时通过 `markdown_to_docx` 转换

**灵活性**：模板格式完全自由，不需要 `{field_name}` 标记，换模板不需要改代码。

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
          ├── Header: [☰ History] [title] [+New] [×Close]
          ├── HistoryPanel (可折叠会话列表)
          ├── Messages Area
          │   ├── PlanBlock (计划进度组件)
          │   ├── ReactMarkdown (回复渲染)
          │   └── Download button (report_ready 时显示)
          └── Input Area: [textarea] [Send/Stop]
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

### 上下文保护

防止对话过长超出模型上下文窗口：
- **消息条数限制**: 每个 conversation 最多保留 50 条消息（旧消息自动裁剪）
- **字符数限制**: Orchestrator 发送给 LLM 时，历史裁剪至最近 10 条、总计 4000 字符以内
- **双重保护**: 条数限制保护存储，字符限制保护 LLM 上下文

### 前端会话管理

- 会话列表：☰ 按钮展开历史面板，显示所有 conversation 的首条消息预览
- 切换会话：点击历史项加载对应会话消息
- 新建会话：[+] 按钮创建新 conversation
- 删除会话：hover 时显示删除按钮，调用 DELETE API

---

## Model Lifecycle

```
本地 Qwen (OpenVINO INT8，模型由 LLM_MODEL_PATH 指定) 作为独立服务运行 (port 9905):

  LLM Service (llm_serving/app.py):
    - 启动时加载模型到 GPU，常驻内存
    - 提供 OpenAI 兼容接口: POST /v1/chat/completions
    - model_lock 保证同一时间只处理一个请求
    - 支持 stream/non-stream 两种模式

  Agent/Summarizer 通过 HTTP 调用:
    LLMServiceClient → POST http://127.0.0.1:9905/v1/chat/completions

保护机制:
  - audio_pipeline_lock: Agent 启动前检测，如 ASR/Summary 正在用 LLM 则拒绝
  - model_lock (服务侧): 并发请求返回 429，客户端自动重试
  - sampling 失败自动 fallback 到 greedy decoding
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

### 场景 1: 首次生成完整报告（ReAct + 模板）

```
用户: "帮我生成一份完整的课堂评估报告"

→ ReAct Loop:
  Step 1 [LLM]: "需要完整报告，先查看可用数据"
    Action: get_session_metadata
  Step 2 [LLM]: "需要所有数据，批量收集"
    Actions:
    - get_class_statistics        ← 自动计算参与度
    - get_class_summary
    - get_mindmap
    - get_topic_segmentation
    - get_teacher_transcription   ← 自动计算语速、提问次数
    - get_content_segmentation    ← 自动计算密度、低活跃时段
  Step 3 [LLM]: "数据收集完毕"
    Action: generate_final_report

→ 生成阶段: LLM 填充模板 → class_report.docx + class_report.md
→ 前端显示 Markdown + [Download Word Report] 按钮
```

**LLM 调用次数**: 3 次（规划 × 2 + 生成 × 1）

### 场景 2: 追问已有报告

```
用户: "参与度最低的时间段是哪个？"

→ ReAct Loop:
  Step 1 [LLM]: "先看有什么数据"
    Action: get_session_metadata
  Step 2 [LLM]: "报告已存在，读取报告；补充统计数据"
    Actions:
    - get_class_report
    - get_class_statistics
  Step 3 [LLM]: "数据足够回答"
    Action: generate_final_report

→ 输出: 对话式简短回答（基于实际数据）
```

**LLM 调用次数**: 3 次（规划 × 2 + 生成 × 1）

### 场景 3: 具体问题

```
用户: "根据今天课程内容出5道测验题"

→ ReAct Loop:
  Step 1 [LLM]: "需要课程内容来出题"
    Action: get_session_metadata
  Step 2 [LLM]: "需要摘要和主题分段"
    Actions:
    - get_class_summary
    - get_topic_segmentation
  Step 3 [LLM]: "内容足够出题"
    Action: generate_final_report

→ 输出: 5 道选择题 (Markdown 格式)
```

**LLM 调用次数**: 3 次（规划 × 2 + 生成 × 1）

### 场景 4: 无数据

```
用户: "分析学生参与度"

→ ReAct Loop:
  Step 1 [LLM]: Action: get_session_metadata
  Step 2 [LLM]: Action: get_class_statistics → "NOT available"
  → observations == []
  → 直接返回: "当前无课堂记录数据，请先完成一节课的录制。"
```

**LLM 调用次数**: 2 次（规划），无生成

---

## 文件结构

```
smart-classroom/
├── main.py                              ← FastAPI 启动入口 (port 8000)
├── api/
│   ├── endpoints.py                     ← HTTP endpoints + conversation CRUD
│   └── llm_serving.py                   ← OpenAI-compatible /v1/chat/completions
├── pipeline.py                          ← Pipeline factory (含 run_report)
├── config.yaml                          ← 应用配置
│
├── components/
│   ├── orchestrator.py                  ← Orchestrator 编排层 (会话管理 + 意图分发)
│   ├── intent_router.py                 ← Intent Router (keyword/llm 模式)
│   └── report_agent/                    ← 学情Agent
│       ├── report_agent.py              ← ReAct loop + Fast Path + 流式生成
│       ├── tools.py                     ← 18 工具 + 统计计算 + AgentMemory
│       ├── prompts.py                   ← Prompt 模板 (ReAct/Report/Chat)
│       ├── conversation.py              ← 多轮对话状态管理 (MAX_MESSAGES=50)
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
│   │   └── AgentChatDialog.tsx          ← 浮动对话框 (PlanBlock + Chat + History)
│   ├── services/api.ts                  ← streamAgentChat() + conversation API
│   ├── assets/css/AgentChat.css         ← Agent UI 样式
│   └── i18n/{en,zh}.json               ← 国际化 (agent.* 键)
│
├── dto/report_dto.py                    ← ReportRequest / AgentChatRequest DTO
│
├── openclaw-skills/                     ← OpenClaw Skill 定义
│   ├── classroom-report/SKILL.md        ← 学情Agent → /report/chat
│   ├── classroom-homework/SKILL.md      ← 作业Agent → /homework/chat
│   └── classroom-lesson-prep/SKILL.md   ← 备课Agent → /lesson-prep/chat
│
└── docs/
    └── report-agent-architecture.md     ← 本文档
```

---

## API Endpoints

| Endpoint | Method | 说明 | 调用方 |
|----------|--------|------|--------|
| `/chat` | POST | **统一入口** → Orchestrator 编排 | Frontend UI |
| `/report/chat` | POST | 学情Agent 多轮对话 | OpenClaw / Orchestrator |
| `/report/generate` | POST | 学情Agent 单次报告 | Frontend / OpenClaw |
| `/report/{session_id}` | GET | 获取已保存报告 (Markdown) | Frontend |
| `/report/{session_id}/download` | GET | 下载 Word 报告 | Frontend |
| `/report/template/upload` | POST | 上传自定义 .docx 模板 | Frontend |
| `/homework/chat` | POST | 作业Agent 多轮对话 (future) | OpenClaw |
| `/lesson-prep/chat` | POST | 备课Agent 多轮对话 (future) | OpenClaw |
| `/conversations/{session_id}` | GET | 获取会话列表 | Frontend |
| `/conversations/{session_id}/{id}` | GET | 获取会话消息 | Frontend |
| `/conversations/{session_id}/{id}` | DELETE | 删除会话 | Frontend |
| `/v1/chat/completions` | POST | OpenAI 兼容 LLM 接口 | OpenClaw (本地 Provider) |
| `/agent/chat` | POST | Legacy alias → `/report/chat` | 向后兼容 |
| `/generate-report` | POST | Legacy alias → `/report/generate` | 向后兼容 |

### `/report/chat` Request

```json
{
  "session_id": "20260526-143000-a1b2",     // 可选，不传则用最新 session
  "message": "分析一下今天学生的参与度",
  "output_format": "chat",                   // "report" | "chat" | null
  "conversation_id": "conv_xxx"              // 可选，追问时携带
}
```

### `/report/chat` Response (Streaming JSON Lines)

```json
{"type": "plan", "steps": [...], "conversation_id": "conv_xxx"}
{"type": "step_start", "index": 0, "conversation_id": "conv_xxx"}
{"type": "step_done", "index": 0, "conversation_id": "conv_xxx"}
{"token": "根据课堂数据...", "error": "", "conversation_id": "conv_xxx"}
{"type": "report_ready", "session_id": "xxx", "conversation_id": "conv_xxx"}
```

---

## Orchestrator（编排层）

Orchestrator 是所有用户请求的入口编排层（替代 OpenClaw 的本地方案）。

职责：
1. 管理对话上下文（创建/复用 conversation_id）
2. 意图分类（通过 IntentRouter）
3. 分发到对应 Agent 或直接回答
4. 对话历史裁剪（防止超出模型上下文）

**设计原则**：Agent 只处理领域任务，不处理通用对话。无关问题由编排层直接响应。

```
POST /chat {message: "..."}
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  Orchestrator.handle_chat(request)                   │
│                                                      │
│  1. 解析 session_id                                  │
│  2. 管理 conversation (创建/复用)                    │
│  3. 记录用户消息                                     │
│  4. IntentRouter.route(message) → 意图分类           │
│  5. 分发到已注册的 handler:                          │
│     ├── "general"     → 直接调 LLM 回答              │
│     ├── "report"      → Report Agent (学情)          │
│     ├── "homework"    → Homework Agent (作业)        │
│     └── "lesson_prep" → Lesson Prep Agent (备课)     │
│                                                      │
│  添加新 Agent:                                       │
│    orchestrator.register_handler("name", handler_fn) │
└─────────────────────────────────────────────────────┘
```

### general 路由（编排层直接回答）

当判定为 `general`（闲聊、问候、与课堂无关的问题），编排层直接用 LLM 生成回复：
- 不收集课堂数据
- 不触发 Agent 的 ReAct/Fast Path 流程
- 携带对话历史上下文（裁剪至 4000 字符以内）
- 流式返回，和 Agent 共用同一个前端 token 渲染

### 对话上下文管理

编排层在 Agent 之前统一管理对话上下文：
- 所有 Agent 共享同一个 conversation_id
- 切换 Agent 时前几轮对话作为参考
- 历史消息按字符数裁剪（MAX_HISTORY_CHARS=4000），防止超出模型上下文
- 每个对话最多保留 50 条消息

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

1. **创建 Agent 模块** — `components/{agent_name}/` 目录，包含核心逻辑
2. **添加独立 endpoint** — 在 `api/endpoints.py` 注册 `POST /{agent_name}/chat`
3. **注册到 Orchestrator** — 在 `orchestrator.py` 的 `_register_default_handlers()` 中：
   ```python
   orchestrator.register_handler("homework", self._handle_homework)
   ```
4. **添加意图路由** — 在 `intent_router.py` 添加关键词模式（keyword 模式）或更新 LLM 分类 prompt（llm 模式）
5. **前端** — 复用现有 AgentChatDialog（共享 token 流渲染、Plan 显示、会话管理）
6. **OpenClaw** (可选) — 添加 `openclaw-skills/{agent}/SKILL.md`，指向 `POST /{agent_name}/chat`

### Endpoint 路由规则

```
每个 Agent 独立 URL namespace:
├── POST /report/chat          ← 学情Agent
├── POST /report/generate      ← 学情Agent 单次
├── GET  /report/{id}          ← 学情Agent 查看
├── POST /homework/chat        ← 作业Agent
├── POST /lesson-prep/chat     ← 备课Agent
└── POST /chat                 ← 统一入口 (Orchestrator 自动分发)
```

---

## OpenClaw 部署

### 概述

OpenClaw 是可选的云端编排层。部署后提供更精准的意图识别（使用 GPT-4o/Claude），不部署时由本地 Orchestrator 替代。

### 两种部署模式对比

```
模式 A: 有 OpenClaw (云端编排)
┌──────────┐     ┌──────────┐     ┌────────────────────┐
│  用户    │ ──→ │ OpenClaw │ ──→ │ Smart Classroom    │
│          │     │ (云端)   │     │ /report/chat       │
│          │     │          │     │ /homework/chat     │
│          │     │ 精准意图 │     │ /lesson-prep/chat  │
└──────────┘     └──────────┘     └────────────────────┘

模式 B: 无 OpenClaw (本地编排)
┌──────────┐     ┌────────────────────────────────────────┐
│  用户    │ ──→ │ Smart Classroom                        │
│          │     │ POST /chat → Orchestrator              │
│          │     │   → IntentRouter (keyword/llm)         │
│          │     │   → dispatch to agent handler          │
└──────────┘     └────────────────────────────────────────┘
```

### Prerequisites

- Node.js 22.19+ 或 Node 24
- 至少一个 LLM Provider（云端 API key 或 Smart Classroom 本地 Qwen 服务）
- Smart Classroom 服务已启动（`localhost:8000`，LLM service on `:9905`）

### Step 1: 安装 OpenClaw

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### Step 2: 配置 Provider 和 Agent

编辑 `~/.openclaw/.env`:
```bash
OPENAI_API_KEY=sk-...   # 云端时用 (纯本地时可不配)
```

编辑 `~/.openclaw/openclaw.json`:

#### 模式 1: 纯本地（推荐，无需云端 API key）

本地模型作为 primary，所有对话和 agent 都跑在本地 Qwen 上。

```json5
{
  "gateway": {
    "mode": "local",
    "bind": "loopback",
    "auth": { "mode": "token", "token": "your-secret-token" }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "smart-classroom": {
        // ⚠️ baseUrl 必须带 /v1，本地服务只暴露 /v1/chat/completions
        "baseUrl": "http://127.0.0.1:9905/v1",
        "apiKey": "local",
        "api": "openai-completions",
        // ⚠️ models 不能为空：自定义 OpenAI-compatible provider 必须显式声明模型
        // id 必须与服务返回的模型名一致（GET /health 或 /v1/models 返回的 model 字段，
        // 即 os.path.basename(LLM_MODEL_PATH)）
        "models": [
          {
            "id": "Qwen_Qwen2.5-7B-Instruct_int8",
            "name": "Qwen2.5-7B-Instruct (OpenVINO INT8)",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 32768,
            "maxTokens": 5120
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "smart-classroom/Qwen_Qwen2.5-7B-Instruct_int8" },
      "skipBootstrap": true
    },
    "list": [
      {
        "id": "classroom",
        "default": true,
        "workspace": "~/.openclaw/workspace-classroom",
        "model": { "primary": "smart-classroom/Qwen_Qwen2.5-7B-Instruct_int8" },
        "identity": {
          "name": "学情助手",
          "theme": "classroom analysis assistant",
          "emoji": "📊"
        },
        // skills: 声明该 agent 可用哪些 skill（SKILL.md）。
        // skill 本身靠拷进 workspace/skills/ 自动加载（见 Step 3），这里只控制可见性。
        // 省略此字段 = 该 agent 可用全部已加载的 skill。
        "skills": ["classroom-report", "classroom-homework", "classroom-lesson-prep"],
        // tools.allow: 控制的是 OpenClaw 内置工具（web_fetch 等），与 skill 无关。
        // 纯学情分析不需要联网可删除此字段（默认放开全部内置工具）。
        "tools": { "allow": ["web_fetch"] }
      }
    ]
  }
}
```

> **Skill ≠ Tool（易混淆）**:
> - **Skill**（`classroom-report` 等 SKILL.md）通过 `skills` 字段控制可见性，靠拷进 `workspace/skills/` 自动加载。
> - **Tool**（`web_fetch` 等内置工具）通过 `tools.allow` 控制。
> - 两者是独立机制，**skill 不在 `tools.allow` 里加载**。

#### 模式 2: 云端 primary + 本地 fallback

需要更精准意图识别时，用云端模型做 primary，本地 Qwen 作为降级。需配置 `OPENAI_API_KEY`。

```json5
{
  "models": {
    "mode": "merge",
    "providers": {
      // openai 是 OpenClaw 内置 provider（pi-ai catalog），模型目录自带，
      // 所以空 {} 即可（甚至可整块省略，只要环境有 OPENAI_API_KEY）。
      "openai": {},
      // smart-classroom 是自定义 provider，OpenClaw 不认识它的模型，
      // 因此必须显式写全 baseUrl / api / models。
      "smart-classroom": {
        "baseUrl": "http://127.0.0.1:9905/v1",
        "apiKey": "local",
        "api": "openai-completions",
        "models": [
          { "id": "Qwen_Qwen2.5-7B-Instruct_int8", "name": "Qwen2.5-7B-Instruct (OpenVINO INT8)", "input": ["text"], "contextWindow": 32768, "maxTokens": 5120 }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "openai/gpt-4o",
        "fallbacks": ["smart-classroom/Qwen_Qwen2.5-7B-Instruct_int8"]
      },
      "skipBootstrap": true
    }
  }
}
```

> **配置要点（易踩坑）**:
> 1. **`models` 不能为空** — 自定义 OpenAI-compatible provider 必须在 `models` 里至少声明一个 `{id: ...}`，否则模型引用无法解析，fallback 形同虚设。
> 2. **`baseUrl` 必须带 `/v1`** — 本地服务只暴露 `POST /v1/chat/completions`（见 `llm_serving/app.py`），对 `api: "openai-completions"` 需写成 `http://127.0.0.1:9905/v1`。
> 3. **模型 `id` 必须与服务返回一致** — 服务返回 `os.path.basename(LLM_MODEL_PATH)`（如 `Qwen_Qwen2.5-7B-Instruct_int8`），不是 `Qwen3-8B` 这类别名。换模型时同步更新此处。
> 4. **纯本地把本地模型设为 primary** — 否则没有 `OPENAI_API_KEY` 时会先尝试云端再降级，既慢又易报错。

#### provider 注册 vs primary/fallback 选择

`models.providers` 和 `agents.*.model` 是**两层解耦**的概念：

| | 作用 | 说明 |
|---|------|------|
| `models.providers` | **注册哪些 provider 可用** | 只是"开门"，注册了不代表会被用到 |
| `agents.*.model.primary` | **该 agent 实际用哪个模型**（必填） | 真正"点菜" |
| `agents.*.model.fallbacks` | primary 失败时按序降级（可选） | 不写则只用 primary |

要点：
- **provider 注册 ≠ 会被使用**。即使 `providers` 里配了 `openai`，只要 `primary` 填的是 `smart-classroom/Qwen_...` 且没写 `fallbacks`，就**永远只走本地**，openai 闲置不影响。
- **`fallbacks` 触发的是 provider/model 级失败**（模型不可用 / 报错 / 超时），不是负载均衡；正常永远走 primary，失败才往后试。
- **纯本地最简写法**：只写 `primary: "smart-classroom/Qwen_..."`，不写 `fallbacks`，甚至可把 `"openai": {}` 整块从 `providers` 删掉，更干净。

```json5
// 纯本地最简：永远只走本地，无需 openai、无需 fallbacks
"agents": {
  "defaults": {
    "model": { "primary": "smart-classroom/Qwen_Qwen2.5-7B-Instruct_int8" }
  }
}
```

> **⚠️ Tool calling 限制**: 本地 LLM service (`llm_serving/app.py`) **不支持原生 function/tool calling** —
> `ChatCompletionRequest` 没有 `tools` 字段，请求里的 `tools` 会被忽略，服务只做 `apply_chat_template` + 文本生成。
> OpenClaw 路由到 agent / 加载 SKILL.md 是靠把 skill 内容写进 prompt、让模型以**文本形式**输出工具调用，再由 OpenClaw 侧解析。
> 7B 模型在这种 text-based tool-call 上稳定性有限（OpenClaw 文档也警告过 OpenAI-compatible `/v1` 模式下 tool calling 不可靠，
> 模型可能把工具 JSON 当普通正文输出）。简单聊天不受影响；若发现路由偶尔失败或工具调用被当正文吐出，根因在此，而非 JSON 配置。

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
        ├── classroom-report/SKILL.md      ← 学情Agent → /report/chat
        ├── classroom-homework/SKILL.md    ← 作业Agent → /homework/chat (future)
        └── classroom-lesson-prep/SKILL.md ← 备课Agent → /lesson-prep/chat (future)
```

### Step 4: 启动

```bash
# 启动 Smart Classroom (LLM service + 主服务)
cd /path/to/smart-classroom
python main.py  # localhost:8000, LLM service on :9905

# 启动 OpenClaw
openclaw gateway start
openclaw gateway status
openclaw skills list  # 验证 skills 加载
```

### Step 5: 验证

```bash
# Smart Classroom 健康检查
curl http://localhost:8000/health

# LLM Service 健康检查
curl http://localhost:9905/health

# 通过 OpenClaw 对话测试
openclaw chat "帮我分析今天课堂的学生参与度"
# 预期: OpenClaw 识别意图 → 选择 classroom-report skill → 调用 /report/chat

# 直接调用测试 (绕过 OpenClaw)
curl -X POST http://localhost:8000/report/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "学生参与度怎么样", "output_format": "chat"}'
```

### 模型如何选择 Skill / Agent 路由

OpenClaw 里有两个层面，被"选中"的方式完全不同，别混淆：

| 层面 | 怎么被选中 | 谁决定 |
|------|-----------|--------|
| **Agent**（`agents.list` 里的 `classroom`） | **确定性路由**（channel / bindings 配置） | host 配置，模型不参与 |
| **Skill**（`classroom-report` / `homework` / `lesson-prep`） | **模型读 `description` 自行判断** | 模型 |

> 注意：本架构里的"report / homework / lesson-prep 三个 Agent"，在 OpenClaw 侧
> **是同一个 `classroom` agent 下的三个 skill**。所以"模型识别该用哪个 Agent"，
> 本质是"模型识别该用哪个 skill"。

**模型选 skill 的流程**：
1. 启动时，OpenClaw 扫描 `workspace/skills/`，把每个 skill 的 **`name` + `description`**（仅元数据，非全文）注入系统 prompt。
2. 用户提问时，模型根据各 skill 的 `description` 和 SKILL.md 里的 **"When to Use / Trigger Phrases"** 判断该用哪个。
3. 选中后，OpenClaw 才把该 SKILL.md **完整正文**加载进上下文，模型按其中 "How to Call" 指示调用 `POST /report/chat` 等接口。

**因此识别准确率取决于 SKILL.md 的 `description` 写得是否互斥、清晰**——这是提示工程，不是 JSON 配置：
- ✅ classroom-report → "课后：生成学情报告、分析参与度、出测验题"
- ✅ classroom-homework → "批改/讲解作业、统计错题"（避免出现 "课堂分析" 这类与 report 重叠的词）
- ✅ classroom-lesson-prep → "课前：备课、生成教案、教学设计"

> ⚠️ **本地 7B 的限制**：skill 选择本质是 tool/function calling。本地服务不支持原生 tool calling
> （见前文），OpenClaw 靠文本提示让模型输出选择。skill 少、`description` 区分度高时 7B 基本能选对；
> skill 多且描述含糊重叠时容易选错。多 skill 场景建议优先用模式 2（云端 primary）做意图识别。

### 如何访问 / 切换多个 Agent

如果 `agents.list` 里配了多个 agent（不是 skill），访问哪一个取决于**入口**——agent 选择是确定性的，**不靠模型语义判断**：

| 入口 | 切换 / 路由方式 |
|------|----------------|
| 本地 TUI / CLI（`openclaw chat`） | `/agent <id>` 或 `/agents` 选择；快捷键 `Ctrl+G` 打开 agent picker；也可 `/session agent:<id>:main` 直接跳到另一个 agent 的会话。**不切换则进 `default: true` 的 agent。** |
| 外部 channel（Slack / Telegram / Discord…） | 靠 `bindings` 按**消息来源**（channel / account / 群）自动路由，用户无感。例：`openclaw agents bind --agent homework-agent --bind telegram:ops` |
| broadcast 组 | 同一条消息让**多个 agent 并行响应**（不是选一个）：`broadcast: { strategy: "parallel", "<peer>": ["classroom", "homework-agent"] }` |

外部 channel 的 binding 匹配优先级（高 → 低）：精确 peer → 父 peer（线程继承）→ Guild+roles → Guild → Team → Account → Channel → **默认 agent**。

> **关键判断：多 agent ≠ 语义分流。**
> - 想让用户随便问、系统**自动分流**到 report / homework / lesson-prep → 用 **skill 层**（单 agent + 多 skill，模型读 `description` 自动选），**不需要第二个 agent**。
> - 本地 `openclaw chat` 没有外部 channel，第二个 agent 只能靠 `/agent` **手动切**——意味着用户得自己知道该用哪个，违背"自动识别"的初衷。
> - **第二个 agent 仅在需要"隔离"时才有意义**：不同人设 / workspace / 模型 / 鉴权 / 会话历史。例如"对外客服（云端大模型）" vs "内部学情（本地 Qwen）"各自独立，通常配合不同 channel binding，而非手动切换。

### 数据隐私

即使部署 OpenClaw，**只有用户的问题文本**经过云端：

| 经过 OpenClaw | 留在本地 |
|:---:|:---:|
| 用户问题 | 原始转录 |
| output_format | 学生数据 |
| session_id | 视频分析 |
| conversation_id | 报告内容 |
| | Word 文件 |

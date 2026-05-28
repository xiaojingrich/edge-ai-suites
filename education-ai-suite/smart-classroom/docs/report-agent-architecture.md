# Smart Classroom Report Agent — Architecture Document

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    OpenClaw (可选入口, 云端路由 + 编排)                           │
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
│  ├── POST /agent/chat        ← 学情Agent 直接入口 (OpenClaw 调用)               │
│  ├── POST /generate-report   ← 学情Agent (单次报告)                             │
│  ├── GET  /report/{id}       ← 获取已生成的报告                                │
│  ├── GET  /report/{id}/download ← 下载 Word 格式报告                           │
│  ├── POST /report/template/upload ← 上传自定义报告模板                          │
│  ├── GET  /conversations/{session_id}          ← 会话列表                       │
│  ├── GET  /conversations/{session_id}/{id}     ← 会话消息                       │
│  ├── DELETE /conversations/{session_id}/{id}   ← 删除会话                       │
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

## Report Agent 架构 — 纯 ReAct Agent

Report Agent 是真正的 Agent：**LLM 自主决定调用哪些工具、何时停止收集、何时生成输出**。

```
┌────────────────────────────────────────────────────────────────┐
│                     generate_report() 入口                       │
│                                                                  │
│  model.acquire_model()                                           │
│        │                                                         │
│        ▼                                                         │
│  ┌─────────────────────────────────────┐                        │
│  │  ReAct Loop (LLM 自主决策)           │                        │
│  │                                     │                        │
│  │  Step 1: get_session_metadata       │                        │
│  │  Step 2: LLM 决定调哪些工具         │                        │
│  │         (支持批量调用)              │                        │
│  │  Step 3: generate_final_report      │                        │
│  │                                     │                        │
│  │  最多 6 步，目标 2-3 步             │                        │
│  └───────────────┬─────────────────────┘                        │
│                  │                                               │
│                  ▼                                               │
│  ┌─────────────────────────────────────┐                        │
│  │  Phase 2: 生成阶段                  │                        │
│  │                                     │                        │
│  │  有模板 → LLM 填充字段 → .docx      │                        │
│  │  无模板 → LLM 流式输出 Markdown      │                        │
│  └─────────────────────────────────────┘                        │
│                  │                                               │
│  model.release_model()                                           │
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
- **模糊工具名匹配**：7B 模型可能 typo，自动纠正
- **LLM 错误容错**：推理步骤中 LLM 失败则终止收集，用已有数据生成
- **不可用数据自动跳过**：工具返回 "NOT available" 时不计入 observations

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
└── docs/
    └── report-agent-architecture.md     ← 本文档
```

---

## API Endpoints

| Endpoint | Method | 说明 | 调用方 |
|----------|--------|------|--------|
| `/chat` | POST | **统一入口** → Orchestrator 编排 | Frontend UI |
| `/agent/chat` | POST | 学情Agent 直接入口 (跳过编排) | OpenClaw |
| `/generate-report` | POST | 单次报告生成 | Frontend / OpenClaw |
| `/report/{session_id}` | GET | 获取已保存报告 (Markdown) | Frontend |
| `/report/{session_id}/download` | GET | 下载 Word 报告 | Frontend |
| `/report/template/upload` | POST | 上传自定义 .docx 模板 | Frontend |
| `/conversations/{session_id}` | GET | 获取会话列表 | Frontend |
| `/conversations/{session_id}/{id}` | GET | 获取会话消息 | Frontend |
| `/conversations/{session_id}/{id}` | DELETE | 删除会话 | Frontend |
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

1. **创建 Agent handler** — 异步函数，签名 `async def handler(request, conversation_id, conv_manager)`
2. **注册到 Orchestrator** — 在 `orchestrator.py` 的 `_register_default_handlers()` 或启动时调用：
   ```python
   from components.orchestrator import orchestrator
   orchestrator.register_handler("homework", handle_homework)
   ```
3. **添加意图路由** — 在 `intent_router.py` 添加关键词模式（keyword 模式）或更新 LLM 分类 prompt（llm 模式）
4. **前端** — 复用现有 AgentChatDialog（共享 token 流渲染、Plan 显示、会话管理）
5. **OpenClaw** (可选) — 添加 `openclaw-skills/{agent}/SKILL.md`，对应 `/agent/chat` 直接入口

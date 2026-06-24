# User-Defined Template for AI Summarized Class Reports - Design

## System Architecture

```mermaid
graph TB
    subgraph UI["UI Layer (Main Interface)"]
        A[Report Button] --> B{Report Panel}
        B --> C[Upload Template .docx]
        B --> D[Select Existing Template]
        B --> E[No Template Selected]
        C --> F[Template Ready]
        D --> F
        E --> F
        F --> G[Generate Report Button]
    end

    subgraph Generation["Report Generation (triggered on Generate click)"]
        G --> H{Template Source}
        H -->|Uploaded .docx| I[Validate & Parse with python-docx]
        H -->|Existing Template| J[Load Saved Template]
        H -->|None| K[Use Default Template]
        I -->|Valid| L[Template Structure]
        I -->|Invalid| M[Error Message to User]
        J --> L
        K --> L
    end

    subgraph DataSources["Data Sources (Read from Local Paths)"]
        S1[summary.md]
        S2[mindmap.md]
        S3[Video Analytics<br/>Students Count / Raise Hand / Stand]
        S4[Teacher Speech Speed<br/>ASR Statistics]
        S5[IFPD/OCR Text<br/>Board & Screen Content]
    end

    subgraph LLMGen["LLM Generation"]
        L --> N[LLM/VLM Engine]
        S1 --> N
        S2 --> N
        S3 --> N
        S4 --> N
        S5 --> N
        N --> O[Generated Report .md]
    end

    subgraph Output["Output & Delivery"]
        O --> P[Save Report to Local]
        P --> Q[Generate Report Link]
        Q --> R[User Views Report in Browser]
    end
```

## UI Workflow

```mermaid
sequenceDiagram
    participant Teacher as Teacher (User)
    participant UI as Main UI
    participant Backend as Report Generator
    participant LLM as LLM/VLM

    Teacher->>UI: Click "Report" Button
    UI->>Teacher: Show Report Panel

    alt Upload New Template
        Teacher->>UI: Upload .docx Template
        UI->>Backend: Validate & Parse Template
        Backend-->>UI: Validation Result
        alt Valid
            UI->>Teacher: Show "Template Uploaded Successfully"
        else Invalid
            UI->>Teacher: Show Error (human-readable)
        end
    else Select Existing Template
        Teacher->>UI: Choose from Template List
    else No Template
        Note over UI: Use Default Template
    end

    Teacher->>UI: Click "Generate Report"
    UI->>Backend: Trigger Report Generation
    Backend->>Backend: Read summary.md
    Backend->>Backend: Read mindmap.md
    Backend->>Backend: Read Video Analytics Stats
    Backend->>Backend: Read Teacher Speech Speed
    Backend->>Backend: Read IFPD Text
    Backend->>Backend: Read OCR Output
    Backend->>LLM: Send Data + Template Structure
    LLM-->>Backend: Generated Report Content
    Backend->>Backend: Save Report (.md)
    Backend-->>UI: Return Report Link
    UI->>Teacher: Display Report Link
    Teacher->>UI: Click Link to View Report
```

## Report Content Structure

```mermaid
graph LR
    subgraph Report["Generated Report"]
        R1[Statistical Data]
        R2[Mind Map]
        R3[IFPD Summary]
        R4[Teaching Suggestions]
    end

    subgraph Stats["Statistical Data"]
        R1 --> ST1[Student Count]
        R1 --> ST2[Raise Hand Count]
        R1 --> ST3[Stand Count]
        R1 --> ST4[Teacher Speech Speed]
    end

    subgraph Content["Content Summary"]
        R2 --> C1[Knowledge Structure]
        R3 --> C2[Board/Screen Content Summary]
    end

    subgraph Suggestions["Teaching Suggestions"]
        R4 --> SG1[Engagement Analysis]
        R4 --> SG2[Improvement Recommendations]
    end
```

## Key Design Decisions

| Item | Decision | Reason |
|------|----------|--------|
| Template Format | .docx (Word) | Teachers are familiar with Word |
| Template Parse | python-docx -> JSON structure | Intermediate format for LLM consumption |
| Default Template | Built-in | Ensures report generation works without upload |
| Data Sources | Read from local paths | All processing on edge device, privacy preserved |
| Report Output | Markdown -> Web view | Easy to render, link shareable |
| LLM Role | Text-only generation | Fill template structure with data |

## Scope & Limitations

- Template-driven formatting only, output structure is user-controlled
- All data stays local on the edge device
- LLM/VLM reuse from existing infrastructure (#2635)

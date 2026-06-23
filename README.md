# SpecForge — Natural Language → App Spec Compiler

> Turn any app description into a validated, cross-consistent, executable specification through a 4-stage AI pipeline with a graph-based Validation + Repair Engine.

## Architecture

```
User Prompt
    │
    ▼
[Stage 1] Intent Extraction       → IntentSpec (JSON)        [Groq / llama-3.3-70b]
    │  ▼ Validate → Repair
    ▼
[Stage 2] System Design Layer     → ArchitectureSpec (JSON)  [Google / gemini-2.0-flash]
    │  ▼ Validate → Repair
    ▼
[Stage 3] Schema Generation ×4    → UI / API / DB / Auth     [Google / gemini-2.0-flash, parallel]
    │  ▼ Validate → Repair
    ▼
[Stage 4] Refinement/Consistency  → Merged AppConfig         [OpenRouter / gemini-2.0-flash-001]
    │
    ▼
[Validation Engine]  4 layers: Syntax → Structure → Semantic → Logic
    │
    ▼
[Dependency Graph]   Graph-based broken-ref localization
    │
    ▼
[Repair Engine]      classify → localize → targeted strategy → patch → re-validate
    │
    ▼
[Execution Simulator] Real SQLite DDL + API shape check + UI binding check
    │
    ▼
[Eval Harness]       20 prompts, metrics: success rate / latency / cost / executability
```

## Key Design Decisions

### Why multi-stage, not single-prompt?
- **Partial regeneration**: if DB schema is wrong, regenerate only DB — not everything
- **Independent validation**: each stage has its own schema and validator
- **Cost tiering**: cheap model (Groq) for Stage 1, stronger models (Gemini) for later stages
- **Stability**: smaller, scoped calls vary less than one giant call

### The Repair Engine (core deliverable)
The repair engine does **not** blind full-retry. It:
1. **Classifies** the failure type: `syntax | missing_field | hallucinated_field | cross_layer_mismatch | logical_conflict`
2. **Localizes** via dependency graph: which field broke and exactly which downstream nodes consume it
3. **Chooses** a targeted strategy: `auto_fix | targeted_reprompt | strip_and_log | regen_downstream | regen_business_rule`
4. **Patches** only the broken node (not the entire stage)
5. **Re-validates** the patch
6. **Caps** at 3 retries → escalates to human if still failing

### Graph-based localization
`validation/dependency_graph.py` maintains a directed graph where edges represent "A depends on B".
When a field breaks, `get_consumers(node_id)` returns all downstream nodes transitively affected.
This lets the repair engine say precisely: "DB.users.role changed → API.GET_users and UI.RoleBadge need re-validation."

## Project Structure

```
ai project/
├── backend/
│   ├── pipeline/
│   │   ├── llm_client.py    # Multi-provider: Groq + Google + OpenRouter
│   │   ├── intent.py        # Stage 1 — Intent Extraction
│   │   ├── design.py        # Stage 2 — System Design
│   │   ├── schema.py        # Stage 3 — Schema Generation (4 parallel calls)
│   │   └── refine.py        # Stage 4 — Cross-layer Consistency
│   ├── validation/
│   │   ├── schema_defs.py   # All Pydantic v2 models (single source of truth)
│   │   ├── validator.py     # 4-layer validator
│   │   └── dependency_graph.py  # Graph-based localization
│   ├── repair/
│   │   └── repair_engine.py # Targeted repair orchestrator
│   ├── execution/
│   │   └── executor.py      # SQLite DDL + API/UI simulation
│   ├── eval/
│   │   ├── test_prompts.py  # 20 test prompts
│   │   └── harness.py       # Batch runner + SQLite logging
│   ├── api/
│   │   └── main.py          # FastAPI + SSE streaming
│   └── config.py            # Typed settings + model costs
└── frontend/
    └── src/
        ├── components/
        │   ├── CompilerPage.tsx  # Main UI with SSE consumer
        │   ├── StageTracker.tsx  # Animated pipeline progress
        │   ├── ResultsTabs.tsx   # Tabbed schema viewer
        │   └── MetricsPage.tsx   # Eval dashboard
        ├── types.ts
        ├── App.tsx
        └── index.css            # Vanilla CSS design system
```

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- API keys for Groq, Google AI Studio, OpenRouter

### Backend

```bash
cd backend
pip install -r requirements.txt

# Edit .env with your API keys (already pre-filled)
uvicorn api.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/run` | Full pipeline (SSE streaming) |
| POST | `/api/execute` | Run execution layer on existing AppConfig |
| POST | `/api/validate` | Run 4-layer validation on AppConfig |
| GET | `/api/eval/results` | Latest eval results |
| POST | `/api/eval/run` | Trigger background eval batch |
| GET | `/api/health` | Health check |

## Model Tiering (Cost vs Quality)

| Stage | Provider | Model | Cost/1M tok | Reason |
|-------|----------|-------|-------------|--------|
| Stage 1 | Groq | llama-3.3-70b-versatile | $0.59/$0.79 | Fast extraction |
| Stage 2 | Google | gemini-2.0-flash | $0.075/$0.30 | Reasoning for entity relations |
| Stage 3 | Google | gemini-2.0-flash | $0.075/$0.30 | 4 parallel calls, fast |
| Stage 4 | OpenRouter | gemini-2.0-flash-001 | $0.075/$0.30 | Cross-layer reasoning |
| Repair | Groq | llama-3.3-70b-versatile | $0.59/$0.79 | Speed critical in repair loop |

## Eval Dataset

- 10 normal prompts: CRM, Marketplace, Booking, Blog, Inventory, LMS, E-commerce, Project Mgmt, HR, Analytics
- 10 edge cases: 4 vague, 4 conflicting, 4 incomplete

Metrics tracked: success rate, retries per request, failure type breakdown, latency (p50/p90), executability score, cost per run.

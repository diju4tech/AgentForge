# Product Requirements Document (PRD)
<!--
  INSTRUCTIONS FOR USE
  ====================
  Copy this file to your project root as PRD.md (or the filename set in project.yaml → project.prd).
  Fill in every section. Do NOT leave placeholder text — the Planner agent reads this file
  literally and will produce ambiguous or incorrect tasks if sections are incomplete.

  Sections marked [REQUIRED] must be filled. Sections marked [IF APPLICABLE] can be removed
  if not relevant to your project.

  Delete all HTML comment blocks (like this one) before running `make plan`.
-->

---

## 1. Project Overview

### 1.1 Project Name
<!-- Single line. Used in branch names, PR titles, and documentation. -->
```
<Project Name>
```

### 1.2 Problem Statement
<!--
  2–4 sentences. State the specific pain point this system solves.
  Be concrete — avoid marketing language.
  Example: "Support engineers spend 40% of their time searching across 6 disconnected
  knowledge bases to answer customer tickets. There is no unified search interface."
-->

### 1.3 Solution Summary
<!--
  2–4 sentences describing what will be built, not how.
  Example: "A question-answering assistant that ingests internal knowledge bases,
  indexes them with vector embeddings, and answers natural-language queries with cited sources."
-->

### 1.4 Goals
<!--
  Numbered list of measurable outcomes this project must achieve.
  Each goal must be verifiable (testable or observable).
-->
1.
2.
3.

### 1.5 Out of Scope
<!--
  Explicitly list what will NOT be built. This prevents the Planner from generating tasks
  for features that are out of bounds.
-->
-
-

---

## 2. Users & Stakeholders  [REQUIRED]

### 2.1 Primary Users
<!-- Who uses the system directly? What are their technical capabilities? -->
| User Type | Description | Technical Level |
|---|---|---|
| | | |

### 2.2 User Stories
<!--
  Format: "As a <user type>, I want to <action> so that <outcome>."
  List the top 5–10 stories. These drive the functional requirements.
-->
1. As a ___, I want to ___ so that ___.
2. As a ___, I want to ___ so that ___.

---

## 3. Functional Requirements  [REQUIRED]

<!--
  Group by feature area. For each requirement:
  - Use "MUST", "SHOULD", "MAY" (RFC 2119) to indicate priority
  - Be specific enough that a test can be written for it
  - Do NOT describe implementation — describe behavior
-->

### 3.1 [Feature Area 1 — e.g., Data Ingestion]
- FR-1.1 [MUST] The system MUST accept the following input formats: ___.
- FR-1.2 [MUST] The system MUST process documents up to ___ MB in size.
- FR-1.3 [SHOULD] The system SHOULD support incremental re-ingestion without full re-indexing.

### 3.2 [Feature Area 2 — e.g., Query & Retrieval]
- FR-2.1 [MUST] The system MUST accept natural-language queries via a REST API.
- FR-2.2 [MUST] Every answer MUST include citations referencing the source document and section.
- FR-2.3 [SHOULD] The system SHOULD return results within ___ ms for 95th percentile queries.

### 3.3 [Feature Area 3 — e.g., User Interface / API]
- FR-3.1 [MUST]
- FR-3.2 [SHOULD]

<!-- Add more feature areas as needed -->

---

## 4. Non-Functional Requirements  [REQUIRED]

### 4.1 Performance
<!-- Specify measurable thresholds. Vague targets ("fast", "scalable") are not acceptable. -->
| Metric | Target | Measurement Method |
|---|---|---|
| API response time (p95) | < ___ ms | Load test with ___ concurrent users |
| Ingestion throughput | ___ documents/min | Batch ingestion benchmark |
| System uptime | ___% | Uptime monitoring |

### 4.2 Scalability
<!-- Describe expected load now and future growth. State if distributed processing is required. -->
- Current expected load: ___
- Growth projection: ___
- Horizontal scaling: [Required / Not required]
- Data volume: ___ GB initial, growing at ___ GB/month

### 4.3 Reliability
- Fault tolerance: ___ (e.g., "pipeline must resume from last checkpoint on failure")
- Data durability: ___ (e.g., "no document loss on service restart")
- Retry behaviour: ___ (e.g., "failed ingestion jobs must retry up to 3 times with exponential backoff")

### 4.4 Security
- Authentication: ___ (e.g., API key, OAuth2, JWT)
- Authorisation: ___ (e.g., role-based, per-tenant isolation)
- Data encryption: ___ (e.g., TLS in transit, AES-256 at rest)
- Secrets management: ___ (e.g., environment variables, Vault)
- PII handling: ___ (e.g., no PII stored, PII masked in logs)

### 4.5 Observability
<!-- Be specific. The Builder will wire these into every service. -->
- Logging: ___ (e.g., structured JSON logs, log level configurable via env var)
- Metrics: ___ (e.g., Prometheus-compatible `/metrics` endpoint on each service)
- Tracing: ___ (e.g., OpenTelemetry trace IDs propagated across services)
- LLM observability: ___ (e.g., LangFuse for token counts, latency, prompt versions)
- Alerting: ___ (e.g., alert on error rate > 1% over 5 min)

---

## 5. System Architecture  [REQUIRED]

### 5.1 Architecture Style
<!--
  Examples: microservices, monolith, event-driven, serverless, pipeline-based.
  State clearly — this determines how the Planner decomposes tasks.
-->
Architecture style: ___

### 5.2 High-Level Component Diagram
<!--
  Describe components and their relationships in text. Use arrows (→) for data flow.
  Example:
    Client → API Service → Retrieval Service → Qdrant (vector DB)
                         ↘ Ingestion Service → Qdrant + PostgreSQL
-->
```
<Draw your component diagram here using ASCII or text notation>
```

### 5.3 Services / Modules  [REQUIRED]
<!--
  List every service or module that will be built.
  This is the primary input for the Planner's task decomposition.
  Each service becomes one or more tasks in the task queue.
-->
| Service / Module | Responsibility | Exposes | Consumes |
|---|---|---|---|
| `service-name` | What it does | REST API on port ___ | Calls ___ service |
| | | | |

### 5.4 Data Flow
<!--
  Step-by-step description of how data moves through the system for each key scenario.
  Example scenarios: document ingestion, user query, error handling.
-->

**Scenario: [e.g., Document Ingestion]**
1.
2.
3.

**Scenario: [e.g., User Query]**
1.
2.
3.

---

## 6. Technology Stack  [REQUIRED]

<!--
  CRITICAL: Be explicit and exhaustive. The Planner copies these constraints into every task.
  The Builder and Reviewer enforce them strictly. Ambiguity here causes rejected tasks.

  For each item, specify: the technology, the version (if relevant), and WHY it was chosen
  (helps the Builder make compatible sub-choices).
-->

### 6.1 Languages & Runtimes
| Purpose | Technology | Version | Notes |
|---|---|---|---|
| Backend services | | | |
| Scripting / tooling | | | |

### 6.2 Frameworks & Libraries
| Purpose | Library | Version | Notes |
|---|---|---|---|
| API framework | | | e.g., FastAPI — chosen for async support and auto OpenAPI docs |
| Agentic framework | | | e.g., LangChain + LangGraph |
| Testing | | | e.g., pytest |
| HTTP client | | | e.g., httpx |

### 6.3 Databases & Storage
| Purpose | Technology | Version | Notes |
|---|---|---|---|
| Vector storage | | | e.g., Qdrant — chosen for hybrid search support |
| Relational / metadata | | | |
| Cache | | | |
| Object storage | | | |

### 6.4 Infrastructure & Deployment
| Purpose | Technology | Notes |
|---|---|---|
| Containerisation | | e.g., Docker + Docker Compose |
| Orchestration | | e.g., Kubernetes (future), Compose (now) |
| CI/CD | | |
| SCM | | e.g., GitHub |

### 6.5 Observability Stack
| Purpose | Technology | Notes |
|---|---|---|
| LLM tracing | | e.g., LangFuse |
| Metrics | | |
| Logging | | |

### 6.6 Explicitly Forbidden Technologies
<!--
  List alternatives that MUST NOT be used. The Reviewer rejects any implementation using these.
  Example: Flask (use FastAPI instead), localhost URLs (use Docker service names), hardcoded secrets.
-->
- DO NOT use: ___
- DO NOT use: ___

---

## 7. Service Specifications  [REQUIRED]

<!--
  One subsection per service listed in 5.3.
  This is the most detailed section — fill it completely.
  The Builder implements one service at a time from these specs.
-->

### 7.1 Service: `<service-name>`

**Responsibility:** One sentence.

**Port:** ___

**Base URL (Docker DNS):** `http://<service-name>:<port>`

#### API Endpoints
| Method | Path | Request Body | Response | Description |
|---|---|---|---|---|
| POST | `/endpoint` | `{field: type}` | `{field: type}` | What it does |

#### Environment Variables
| Variable | Required | Description | Example |
|---|---|---|---|
| `SERVICE_HOST` | Yes | Host to bind | `0.0.0.0` |
| `SERVICE_PORT` | Yes | Port to listen on | `8001` |
| `DEPENDENCY_URL` | Yes | URL of dependent service | `http://other-service:8002` |

#### Dependencies
- Calls: ___ (service name, endpoint)
- Depends on: ___ (database, queue, etc.)

#### Error Handling
- ___ (e.g., "Return HTTP 422 with `{error: string}` on invalid input")
- ___ (e.g., "Retry upstream calls up to 3 times before returning HTTP 503")

#### Acceptance Criteria
- [ ]
- [ ]

---

### 7.2 Service: `<service-name>`
<!-- Repeat section 7.1 for each service -->

---

## 8. Data Models  [REQUIRED]

<!--
  Define all core entities. Include field names, types, constraints, and relationships.
  The Builder derives database schemas and API request/response models from this section.
-->

### 8.1 `<EntityName>`
```
{
  "field_name": "type",          // description, constraints (e.g., required, max length)
  "field_name": "type",
}
```
Relationships: ___

### 8.2 `<EntityName>`
<!-- Repeat for each entity -->

---

## 9. Integration Points  [IF APPLICABLE]

<!--
  External APIs, third-party services, or internal systems this project integrates with.
  The Builder needs exact details to implement these without guessing.
-->

### 9.1 `<Integration Name>`
- Type: REST API / Webhook / SDK / Message Queue
- Authentication: ___
- Base URL: ___
- Endpoints used: ___
- Rate limits: ___
- Failure handling: ___

---

## 10. Infrastructure & Deployment  [REQUIRED]

### 10.1 Local Development
- All services run via: ___ (e.g., `docker compose up`)
- Required local dependencies: ___ (e.g., Docker Desktop ≥ 4.x)
- Seed data / fixtures needed: ___

### 10.2 Docker Requirements
- Each service MUST have its own `Dockerfile` at: `services/<service-name>/Dockerfile`
- Base image preference: ___ (e.g., `python:3.11-slim`)
- All inter-service communication via Docker DNS names: `http://<service-name>:<port>`
- Shared `docker-compose.yml` at project root defines all services and networks

### 10.3 Environment Configuration
- All config via environment variables — no hardcoded values anywhere
- `.env` file at project root for local development (gitignored)
- `.env.example` committed to SCM with placeholder values

### 10.4 Networking
<!-- List all service-to-service connections the Planner needs to know about -->
| From Service | To Service | Protocol | Port |
|---|---|---|---|
| | | HTTP | |

---

## 11. Testing Requirements  [REQUIRED]

<!--
  The Builder writes tests for every task. These requirements constrain what must be covered.
-->

### 11.1 Unit Tests
- Framework: ___ (e.g., pytest)
- Minimum coverage: ___%
- Location: `tests/unit/`
- Must be deterministic — no external service calls (mock/stub all dependencies)

### 11.2 Integration Tests  [IF APPLICABLE]
- Location: `tests/integration/`
- Run against: ___ (e.g., real Qdrant instance via docker-compose)
- Trigger: ___ (e.g., `make test-integration`, separate from `make test`)

### 11.3 Test Data
- ___ (e.g., "Sample PDFs for ingestion tests are in `tests/fixtures/`")
- ___ (e.g., "Use deterministic seed data — no random generation in test fixtures")

---

## 12. Documentation Artifacts  [REQUIRED]

<!--
  List the documentation files the Builder/agents must produce.
  The Planner generates tasks for each of these.
-->
| Artifact | Path | Contents |
|---|---|---|
| Architecture diagram | `docs/ARCHITECTURE.md` | System diagram, component interactions, data flow |
| System design | `docs/SYSTEM_DESIGN.md` | Design decisions, trade-offs, scaling strategy |
| Low-level design | `docs/LLD.md` | Class diagrams, API specs, data schemas |
| How-to guide | `docs/HOWTO.md` | Setup instructions, deployment steps, usage examples |

---

## 13. Constraints & Guardrails  [REQUIRED]

<!--
  Hard rules the Planner embeds into every task. Violations cause automatic rejection by the Reviewer.
-->

### 13.1 Coding Constraints
- ___ (e.g., "All service configuration via environment variables — no hardcoded hosts, ports, or secrets")
- ___ (e.g., "Services must be stateless — no in-memory state that survives a restart")
- ___ (e.g., "Use Docker service names for all inter-service HTTP calls")

### 13.2 Process Constraints
- One component per PR — no bundling multiple services in one commit
- Each task must include code + tests + documentation updates
- No task may modify services outside its defined scope

### 13.3 Dependency Constraints
- ___ (e.g., "Do not introduce dependencies not listed in Section 6")
- ___ (e.g., "All new dependencies must be added to the service's requirements.txt")

---

## 14. Glossary  [IF APPLICABLE]

<!--
  Define domain-specific terms. The Planner uses these to generate unambiguous task descriptions.
-->
| Term | Definition |
|---|---|
| | |

---

## 15. Open Questions  [IF APPLICABLE]

<!--
  Document unresolved decisions before running `make plan`.
  The Planner will make assumptions where questions are left open — resolve them first.
-->
| # | Question | Owner | Resolution |
|---|---|---|---|
| 1 | | | |

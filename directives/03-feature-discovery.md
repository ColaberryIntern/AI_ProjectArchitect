# Directive: Feature Discovery

## Purpose

Translate a validated idea into a coherent set of features classified as core (MVP) or optional, with a defensible build order. Features span both **functional** (what the product does) and **architectural** (how it's built) layers.

## Inputs

- Approved ideation summary (from `state.ideation`)
- Current phase must be `feature_discovery`

## Feature Catalog

The system generates a project-specific catalog of **50-75 features** across up to **13 categories** via a one-time LLM call. Falls back to a 71-feature generic catalog if LLM is unavailable.

### Functional Categories (7)
| Category | Description |
|---|---|
| Core Functionality | Registration, dashboard, search, content management, role management |
| AI & Intelligence | Recommendations, content generation, NLP search, adaptive systems |
| User Experience | Responsive design, accessibility, onboarding, dark mode |
| Assessment & Progress | Progress tracking, skill assessment, goal setting, feedback |
| Engagement | Notifications, gamification, social features, discussion forums |
| Integrations | API access, calendar sync, OAuth, webhooks, payment, SSO |
| Analytics & Reporting | Usage analytics, custom reports, export, real-time dashboards, A/B testing |

### Architectural Categories (6)
| Category | Description |
|---|---|
| Architecture & Infrastructure | Microservices, modular monolith, API gateway, background jobs, message queues, caching, event-driven, database-per-service |
| Security & Compliance | RBAC, MFA, encryption at rest, GDPR toolkit, audit logging, secrets management, rate limiting |
| ML & Model Layer | Recommender systems, time-series forecasting, transformer NLP, model versioning, feature stores, model evaluation, data pipelines |
| DevOps & Deployment | CI/CD, staging environments, blue-green deployment, IaC, feature flags, container orchestration |
| Observability & Monitoring | Application logging, APM, AI model monitoring, alerting, health checks, distributed tracing |
| Testing & QA | Unit testing frameworks, integration testing, load testing, security testing, AI evaluation suites |

### Layer Classification
Categories are grouped into two layers at render time:
- **Functional** (Product Features tab): What the product does for users
- **Architectural** (Architecture & Ops tab): How the system is built and operated

Layer is computed via `CATEGORY_LAYERS` dict lookup — NOT stored in feature data. Old catalogs with unknown categories default to the functional layer.

## Mutual Exclusion Rules

Certain features are mutually exclusive — only one from each group may be selected:

| Group | Conflicting Features | Rationale |
|---|---|---|
| Architecture Style | Microservices vs Modular Monolith | Fundamental architectural choice — pick one |
| Deployment Strategy | Blue-Green Deployment vs Canary Releases | Choose one deployment strategy |

Mutual exclusion is enforced:
- **Server-side**: `check_mutual_exclusions()` runs before saving selections; violations redirect back with error
- **Client-side**: `checkExclusions()` shows advisory warnings in real-time as checkboxes change

## Steps

### Step 1: Translate Ideas into Features
For each validated dimension from ideation, ask:
- What must the system do to solve this problem?
- What decisions must it support or automate?
- What outputs must it produce?
- What inputs must it accept?

Each answer becomes a candidate feature.

### Step 2: Apply Feature Definition Rule
Every feature must pass all 4 criteria:
- Has a clear purpose
- Is testable
- Is explainable to a junior builder
- Directly supports the core problem

If a candidate fails any criterion, it is not a feature — discard or rephrase.

### Step 3: Classify Features
For each valid feature, classify immediately:

**Core Features** (MVP):
- Required for the system to deliver value
- Blocking for initial usefulness
- If removed, the product stops working

**Optional Features**:
- Improve experience but don't block progress
- Enable future scale
- Clearly labeled and may be deferred

Record via `state_manager.add_feature()`.

### Step 4: AI Feature Suggestions
The system may suggest additional features only when:
- A known gap exists
- A common pattern applies
- A manual step could be automated

Every suggestion must include: the problem it solves, why it matters, core or optional classification.

### Step 5: Apply Anti-Overengineering Guardrails

1. **Feature-to-Problem Mapping**: Every feature must map to exactly one problem. If it maps to none, remove it.
2. **Intern Test**: "Can an intern explain why this feature exists?" If not, clarify or remove.
3. **Build Order Discipline**: Order by Dependency -> Value -> Risk. Low-value high-risk items are deferred.
4. **Explicit Deferment**: Deferred features are documented with reason — not forgotten.
5. **Mutual Exclusion**: Conflicting features cannot both be selected (enforced server-side).

### Step 6: Present and Approve
Present:
- Core features list with rationale and build order
- Optional features list with rationale and deferment notes
- Features organized by layer tabs (Product Features / Architecture & Ops)

On user approval, call `state_manager.approve_features()` and advance to `outline_generation`.

## UI Layout

The feature discovery page uses a **tabbed layout**:

1. **Layer tabs** at top: "Product Features" | "Architecture & Ops"
   - Each tab shows a count badge of selected features in that layer
2. **Categories as sections** within each tab (2-column grid)
3. **Category-level "All" / "Clear" buttons** on each category header
4. **Error banner** for mutual exclusion violations (server-side redirect)
5. **Right pane** shows live-updated selected feature tally

## Scope Limits by MVP Level

| MVP Scope | Min Features | Max Features |
|---|---|---|
| proof_of_concept | 1 | 15 |
| core_only | 3 | 20 |
| core_plus_ai | 5 | 30 |
| full_vertical | 8 | 45 |
| platform_foundation | 10 | 60 |

## Outputs

- `features.core` populated with classified features
- `features.optional` populated with classified features
- `features.approved` is `True`
- Phase advanced to `outline_generation`

## Edge Cases

- User wants to promote an optional feature to core: Allow with justification. Update classification.
- Too many features discovered: Apply guardrails aggressively. Suggest deferring.
- No optional features: Acceptable — not every project has deferrable features.
- Old project with 25-feature catalog: Renders entirely under "Product Features" tab (backward compatible).
- Mutual exclusion violation: Server redirects back with error message; user must deselect one.

## Safety Constraints

- Never promote optional features to core without user approval
- Never build deferred features into the outline
- Never skip the feature-to-problem mapping check
- Never allow mutually exclusive features to be saved together
- Resist scope explosion

## Verification

- Core features list exists and is non-empty
- Each core feature has: id, name, description, rationale, problem_mapped_to, build_order
- Optional features (if any) are explicitly labeled with deferment status
- Build order is defensible (dependency -> value -> risk)
- No mutual exclusion violations in saved selections
- User has approved the feature set

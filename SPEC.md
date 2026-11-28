# Job Right · Preference Replay: LangMem proposal → replay → interrupt → revision-checked commit

## Goal
Show, end to end and offline, how a job seeker's preference change is proposed by LangMem from free-text feedback, replayed against their earlier wanted/rejected labels, paused for approval in a LangGraph workflow that survives process restarts, and committed to SQLite only if the profile it was based on is still current.

## Risk: High (concurrent and duplicate decisions write persisted preference data)

## Scope
- Not doing: job applications or ApplyPilot, form filling, submissions, scraping, outreach, browser automation, real candidate data, credentials, remote tracing, web UI, paid model calls in tests or the demo, a LangMem-vs-baseline comparison (dropped by the owner).
- Ask first: any publication other than a fast-forward push of this repo's `origin/main` after clean-clone verification.

## Key decisions
- Package `job_right` (src layout, uv, Python >=3.11,<3.14, committed `uv.lock`). CLI `job-right` with `init | profile | recommend | propose | show | approve | reject | history | demo`, plus `--data-dir` (default `./.job-right`).
- Graph: `StateGraph` with a `TypedDict` state holding JSON-native values only. The chat model and store reach nodes through LangGraph runtime context and are never checkpointed. Nodes: `load_profile → propose_update → validate_candidate → (no change → END) → replay_labels → record_proposal → human_review (interrupt) → commit_decision`. Checkpointer: `SqliteSaver` on `checkpoints.sqlite`; thread id = proposal id.
- Proposal stage: a real `langmem.create_memory_manager(model, schemas=[JobSeekerProfile], enable_inserts=False, enable_updates=True, enable_deletes=False)`. Its single existing memory is the previous typed profile, and the feedback arrives as messages built with a LangChain Core `ChatPromptTemplate`. The validated candidate minus the current profile gives the typed edit (`list[ProfileChange]`).
- Model is any LangChain Core `BaseChatModel`. The default is `FixtureChatModel`: deterministic regex rules that answer trustcall's `PatchDoc` tool call. It is labelled "fixture, not an LLM" in code, CLI output and README. The opt-in live path is `--model provider:name` via `langchain.chat_models.init_chat_model` with the user's own credentials or a local OpenAI-compatible server.
- App store `job_right.sqlite`: `jobs`, `labels`, `profile_revisions(revision PK, profile, proposal_id UNIQUE)`, `proposals(id PK, base_revision, current, proposed, changes, replay, status pending|approved|rejected|stale, applied_revision)`. `decide()` is one `BEGIN IMMEDIATE` transaction: the first decision wins; approve writes `base_revision + 1` only if `base_revision == MAX(revision)`, otherwise marks the proposal `stale`.
- Deterministic filters (role, work mode with per-employer exceptions, location, salary floor, seniority, excluded industry) each return a reason plus a concrete fix. Ranking score = 10 × matched preferred skills + salary_max // 10 000, ties broken by salary then id.
- Remote LangSmith tracing is forced off unless `JOB_RIGHT_ALLOW_LANGSMITH=1`.

## Acceptance examples
1. Rev 1 accepts remote+hybrid; wanted job J is hybrid at Fernhill Freight. `propose "Only show me remote roles"` → the review lists J under "wanted jobs this change would hide", with the work-mode reason and a fix (keep hybrid, or add an employer exception for Fernhill Freight). Only a pending proposal is stored.
2. `propose` exits at the interrupt; `approve <id>` in a new OS process resumes from SQLite and writes revision 2 = the proposed profile.
3. `reject <id>` → proposal `rejected`; current profile and revision unchanged.
4. Proposals A and B are both based on rev 2. Approve B → rev 3; approve A → `stale`; the profile stays at rev 3 with B's change intact.
5. Approving twice, or rejecting then approving, reports the recorded first decision, and exactly one revision exists. Racing decisions (approve/approve, approve/reject) produce at most one revision, and that revision is byte-identical to the candidate shown in the review (its digest travels with the approval).
6. Store committed but checkpoint not written (crash simulation) → resuming the thread does not apply twice.
7. LangMem with the fixture, across turns: "Only show me remote roles" approved, "add Rust to my preferred skills" approved, then the reversal "hybrid roles are fine again" approved → each change applied; unrelated fields and the employer exception retained exactly throughout.
8. Feedback that maps to no change → the workflow ends without an interrupt and stores no proposal.
9. A candidate violating the schema or invariants (e.g. empty work modes) is never proposed.
10. `uv run job-right demo` runs the scenario in fresh subprocesses against a temporary data dir and exits 0.

## Default assumptions (strike any you disagree with)
- Synthetic data only, written from scratch: about 14 fictional listings, 8 labels, one profile.
- "Agreement" = a wanted job is still shown, or a rejected job is filtered out. Replay also lists rejected jobs newly shown.
- Stale proposals are not auto-rebased; the user re-runs `propose` (the demo shows this).
- The live path is tested against a local OpenAI-compatible stub server; no paid provider is called.
- Checks = ruff lint + ruff format check + pytest. No type checker, since that would add a new dev dependency.
- SPEC.md and `.claude/verify.sh` are committed; `.claude/tests-locked` is git-ignored.

## Change log
<!-- date · finding → decision -->
- 2026-11-21 · Coordinator review → case 7 adds a reversal of an approved preference; case 5 adds "racing decisions persist only the reviewed candidate" (approval carries the reviewed candidate's digest).
- 2026-11-22 · Test-mapping review → example 1 names the actual fixture employer (Fernhill Freight); agreement figures in tests describe the synthetic fixtures only.

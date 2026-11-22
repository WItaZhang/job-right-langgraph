# Job Right · Preference Replay

A standalone project for reviewing changes to a job seeker's preferences. It grew out of [Job Right](https://github.com/WItaZhang/codex-job-agent), which filters and ranks job postings. This repository focuses on the moment a user changes a rule.

Suppose a user who previously saved a hybrid role asks for remote-only recommendations. The proposed rule would hide a job they liked. The review should show them which job would disappear before they decide whether to keep the change.

## Workflow under development

1. Use LangChain Core to turn preference feedback into a structured edit.
2. Replay the old and proposed rules against the same set of rated jobs. Show which previously wanted jobs the change would hide.
3. Pause the LangGraph workflow for approval. On approval, compare the reviewed profile version with the current version in SQLite before saving.

The planned demo uses fictional listings and ratings and runs locally. The [earlier LangChain Core adapter](https://github.com/WItaZhang/codex-job-agent/blob/c53e782b8a0db0f48be8abb019098fb827c31d1e/src/applypilot_agent/evaluation/profile_memory_codex.py) lives in the original project.

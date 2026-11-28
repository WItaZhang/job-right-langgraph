# Job Right · Preference Replay

Review a change to your job preferences before it reshapes your recommendations. Try a remote-only rule and see exactly which saved hybrid role it would hide, why it drops out, and which employer exception could keep it.

## Open the local product

From a clone of this repository with [uv](https://docs.astral.sh/uv/) installed, run:

```sh
uv run --locked job-right web
```

Open **[Job Right on this computer](http://127.0.0.1:8765/)**. The first launch installs the locked dependencies and seeds 14 fictional jobs, eight ratings and profile v1. Subsequent launches reuse the SQLite files in `.job-right/`. Stop the server with Ctrl+C. Use `--data-dir PATH` for another workspace or `--port PORT` to choose another loopback port.

### Try a review

1. Browse **Matches**, switch to **Filtered** to see why a listing was excluded, then open **Preferences** to inspect the saved profile.
2. On a narrow screen, tap **Preview a change** to jump to **Refine your matches**. There, choose **Remote only** and **Preview change**. Compare the old and proposed work arrangements. Fernhill Freight’s saved hybrid role would disappear. Expand **Listing changes** to see ranks and filter reasons. Refresh the page or reopen the latest pending review from Matches.
3. Choose **Reject change**. Profile v1 remains available in Preferences. Try **Keep Fernhill** (also available as a suggested exception in a pending review) to preview the remote-only rule with an employer exception. Approve it, then inspect the updated matches and the saved revision in **Activity**.
4. To try a version conflict, leave a **Salary floor** review pending. Open Matches in a second tab, preview **Prefer Rust** and approve it. Return to the first tab and approve the older review. Select **Use this feedback** to generate a fresh preview from the current profile.

The Web UI and CLI share the same files, LangMem manager, LangGraph checkpoints and decision transaction. The server binds to loopback and checks the host, origin and form token for writes. Jobs, employers and prior ratings in this workspace are synthetic.

## Run the offline CLI demo

From a clone with [uv](https://docs.astral.sh/uv/) installed, run:

```sh
uv run --locked job-right demo
```

The demo initializes a temporary SQLite workspace with 14 synthetic listings, eight earlier ratings, and one fictional profile. Each step runs in a fresh process. Watch the Fernhill Freight review, reject the first rule, approve a narrower change, and retry an older proposal against the updated profile.

For a persistent workspace, run these commands from the same directory. Skip `init` if the Web product has already created the workspace:

```sh
uv run --locked job-right init
uv run --locked job-right recommend
uv run --locked job-right propose --id remote "Only show me remote roles."
uv run --locked job-right show remote
uv run --locked job-right approve remote
uv run --locked job-right profile
uv run --locked job-right history
```

Use `reject ID` to decline a proposal. Pass `--data-dir PATH` to choose a workspace (default `.job-right/`) and `--json` for machine-readable output.

## Model and storage

The default model is a deterministic offline fixture, not an LLM. It recognizes phrases such as “Add Rust to my preferred skills,” “Raise my salary floor to $160k,” and “Hybrid roles are fine again.” LangMem uses the existing typed profile as its single memory. A real LangGraph workflow replays the proposed changes against the same earlier ratings and pauses for review in SQLite.

An explicit `web --model provider:model` or `propose --model provider:model "feedback"` uses the named LangChain provider. Supply its integration and credentials, or configure an OpenAI-compatible local server with `OPENAI_BASE_URL` and an appropriate `OPENAI_API_KEY`. The model receives the profile and feedback. The listings and replay run locally. Remote LangSmith tracing requires an explicit `JOB_RIGHT_ALLOW_LANGSMITH=1` plus your tracing configuration. No paid model is required for the demo or tests.

An approval carries the reviewed candidate digest. A single SQLite transaction saves that exact candidate only while its base revision is current. Concurrent or retried decisions return the first recorded result. A stale review can be proposed again against the latest profile.

## Verify

```sh
./.claude/verify.sh
```

This runs locked Ruff lint, format checks and pytest, including subprocess resume, racing decisions, crash recovery and a live-provider adapter against a loopback test stub.

The browser check starts temporary local servers for desktop and mobile viewports. Install the test browser once, then run:

```sh
uv run --locked --group browser playwright install chromium
uv run --locked --group browser python checks/browser_smoke.py
```

This project grew out of [Job Right](https://github.com/WItaZhang/codex-job-agent). See the [workflow specification](SPEC.md) and [license](LICENSE).

## Tailor a résumé and cover letter

Select **Tailor documents** on a listing or **Bring a job description** for your own role. Paste a résumé, choose a text file, or try the clearly labeled fictional example. **Prepare résumé & letter** saves the pair. On your next role, the last base is already filled in.

The offline editor prioritizes relevant accomplishments within each employer or project. It preserves dates, metrics and source text. The letter draws on specific lines of the tailored résumé. Check **Evidence used** and **Unmatched terms**, edit either document, and revisit earlier versions. Saving a résumé revision rebuilds its letter. Download both documents and the evidence map as a ZIP, or open **Print / PDF** for a single document.

**Resume and Interview interchange.** Download the listing’s `jd.md` for a tailoring run. Import the run’s text résumé and optional `cover_letter/letter.txt` through **Import existing files**. To read a selected `.tex` base locally, start with `uv run --locked job-right web --resume-project /path/to/resume-and-interview`. This calls that trusted checkout’s text converter on the selected file. Generation uses the local document editor. The original project’s evaluator and publishing workflow remain available in that checkout.

For model-assisted writing locally, start with `web --writer-model provider:model` and select that model in the document form. Only then are the role, experience bullets and optional personal note sent to the provider. The writer prioritizes existing bullets and cites its letter, followed by a separate factual check. Review the finished version before use. Hosted drafting stays offline.

## Hosted workspace

The public Flask app uses a signed, secure browser cookie to identify a private workspace. Vercel Private Blob stores its application database and LangGraph checkpoint database together with conditional writes. Public visitors can use the sample catalog or supply their own job description and base résumé. Keep the cookie to reopen saved work in this browser. The local CLI continues to use its own SQLite directory.

The production entry point is `app.py`. Set a stable random `JOB_RIGHT_SESSION_KEY` and connect a private Blob store with `BLOB_READ_WRITE_TOKEN`. The hosted product uses offline rules. Job descriptions and applicant materials stay inside the visitor’s hosted workspace.

Run the document browser check with `uv run --locked --group browser python checks/documents_smoke.py`. Both browser checks accept `--url` to exercise a deployed product in a fresh browser context.

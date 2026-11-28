"""Shared fixtures: a seeded synthetic store, a workflow on a temp data dir, and a CLI runner.

Every test uses its own temporary data directory and the synthetic dataset only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from job_right.domain import JobSeekerProfile, diff_profiles
from job_right.replay import replay
from job_right.store import STORE_FILENAME, ProfileStore, Proposal
from job_right.synthetic import INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def seeded_store(data_dir: Path) -> Iterator[ProfileStore]:
    store = ProfileStore(data_dir / STORE_FILENAME)
    store.seed(INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS)
    yield store
    store.close()


@pytest.fixture
def workflow(seeded_store: ProfileStore, data_dir: Path):
    from job_right.graph import Workflow

    wf = Workflow(data_dir)
    yield wf
    wf.close()


def profile_with(base: JobSeekerProfile, **changes) -> JobSeekerProfile:
    return JobSeekerProfile.model_validate({**base.model_dump(mode="json"), **changes})


def make_proposal(store: ProfileStore, proposal_id: str, **changes) -> Proposal:
    """Record a pending proposal against the current revision without going through LangMem."""
    current = store.current()
    proposed = profile_with(current.profile, **changes)
    proposal = Proposal(
        id=proposal_id,
        feedback=f"test change {changes}",
        model_label="test",
        base_revision=current.revision,
        current=current.profile,
        proposed=proposed,
        changes=diff_profiles(current.profile, proposed),
        replay=replay(current.profile, proposed, store.jobs(), store.labels()),
    )
    return store.record_proposal(proposal)


def run_cli(data_dir: Path, *args: str, env: dict[str, str] | None = None, check: bool = True):
    """Run `python -m job_right` in a fresh OS process, as a user would run `job-right`."""
    proc = subprocess.run(
        [sys.executable, "-m", "job_right", "--data-dir", str(data_dir), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=300,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"job-right {' '.join(args)} exited {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def run_cli_json(data_dir: Path, *args: str, env: dict[str, str] | None = None) -> dict:
    return json.loads(run_cli(data_dir, "--json", *args, env=env).stdout)

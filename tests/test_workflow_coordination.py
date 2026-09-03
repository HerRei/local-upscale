from __future__ import annotations

import pytest

from scripts import workflow_coordination


def test_find_blocking_runs_filters_name_status_and_current_run() -> None:
    runs = [
        {
            "id": 10,
            "name": "CI",
            "status": "in_progress",
            "html_url": "https://example.invalid/10",
        },
        {"id": 11, "name": "CI", "status": "completed"},
        {"id": 12, "name": "Unrelated", "status": "queued"},
        {"id": 13, "name": "CI", "status": "waiting"},
        {"id": 14, "name": "CI", "status": "queued", "head_sha": "same-head"},
    ]

    blocking = workflow_coordination.find_blocking_runs(
        runs,
        blocked_workflows={"CI"},
        current_run_id=13,
        ignored_head_shas={"same-head"},
    )

    assert blocking == [
        workflow_coordination.ActiveRun(
            database_id=10,
            name="CI",
            status="in_progress",
            url="https://example.invalid/10",
        )
    ]


def test_fifo_filter_ignores_newer_peers_but_always_blocks_same_head() -> None:
    runs = [
        {"id": 40, "name": "CI", "status": "queued", "head_sha": "older"},
        {"id": 60, "name": "CI", "status": "queued", "head_sha": "newer"},
        {"id": 70, "name": "CI", "status": "queued", "head_sha": "release-head"},
    ]

    blocking = workflow_coordination.find_blocking_runs(
        runs,
        blocked_workflows={"CI"},
        current_run_id=50,
        only_older_runs=True,
        always_block_head_shas={"release-head"},
    )

    assert [run.database_id for run in blocking] == [40, 70]


def test_fail_mode_reports_active_run_without_sleeping(monkeypatch) -> None:
    monkeypatch.setattr(
        workflow_coordination,
        "_request_runs",
        lambda **_kwargs: [{"id": 22, "name": "CI", "status": "queued"}],
    )

    with pytest.raises(RuntimeError, match=r"CI #22 \(queued\)"):
        workflow_coordination.coordinate(
            repository="owner/repo",
            token="not-a-real-token",
            blocked_workflows={"CI"},
            current_run_id=21,
            mode="fail",
            timeout_seconds=10,
            poll_seconds=1,
        )


def test_wait_mode_rechecks_until_slot_is_clear(monkeypatch) -> None:
    responses = iter(
        [
            [{"id": 30, "name": "CI", "status": "in_progress"}],
            [],
        ]
    )
    sleeps: list[int] = []
    monkeypatch.setattr(
        workflow_coordination,
        "_request_runs",
        lambda **_kwargs: next(responses),
    )
    monkeypatch.setattr(workflow_coordination.time, "sleep", sleeps.append)

    workflow_coordination.coordinate(
        repository="owner/repo",
        token="not-a-real-token",
        blocked_workflows={"CI"},
        current_run_id=31,
        mode="wait",
        timeout_seconds=10,
        poll_seconds=2,
    )

    assert sleeps == [2]

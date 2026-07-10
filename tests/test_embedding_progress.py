"""Tests for process-global embedding/Ollama progress state."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def test_pull_progress_snapshot_is_thread_safe() -> None:
    from openbiliclaw.runtime import embedding_progress

    embedding_progress.mark_pull_running("bge-m3")

    def report(i: int) -> dict[str, object]:
        embedding_progress.report_pull("downloading", i, 1000)
        snap = embedding_progress.snapshot()
        assert snap["running"] is True
        assert snap["model"] == "bge-m3"
        assert int(snap["completed"]) >= 0
        assert int(snap["total"]) == 1000
        return snap

    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = list(pool.map(report, range(1, 200)))

    assert snapshots
    active = embedding_progress.snapshot()
    assert active["running"] is True
    assert active["done"] is False
    assert active["ok"] is False
    assert "正在下载 bge-m3" in str(active["status_text"])

    embedding_progress.mark_pull_done(True, "")
    done = embedding_progress.snapshot()
    assert done["running"] is False
    assert done["done"] is True
    assert done["ok"] is True
    assert done["error"] == ""


def test_reset_clears_pull_and_ollama_phase_state() -> None:
    from openbiliclaw.runtime import embedding_progress

    embedding_progress.mark_pull_running("bge-m3")
    embedding_progress.report_pull("downloading", 12, 34)
    embedding_progress.report_ollama_phase("down")

    embedding_progress.reset()

    assert embedding_progress.snapshot() == {
        "running": False,
        "model": "",
        "status": "",
        "completed": 0,
        "total": 0,
        "done": False,
        "ok": False,
        "error": "",
        "started_monotonic": 0.0,
        "status_text": "",
    }
    assert embedding_progress.ollama_phase() == "ready"


def test_ollama_phase_transitions_are_process_global() -> None:
    from openbiliclaw.runtime import embedding_progress

    embedding_progress.report_ollama_phase("down")
    assert embedding_progress.ollama_phase() == "down"

    embedding_progress.report_ollama_phase("starting")
    assert embedding_progress.ollama_phase() == "starting"

    embedding_progress.report_ollama_phase("ready")
    assert embedding_progress.ollama_phase() == "ready"

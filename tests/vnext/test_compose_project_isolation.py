"""Compose deployments must be project-isolated and safe to run concurrently."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_compose_services_do_not_claim_global_container_names() -> None:
    paths = (
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.prebuilt.yml",
        ROOT / "tests/docker_e2e/docker-compose.e2e.yml",
    )
    for path in paths:
        assert "container_name:" not in path.read_text(encoding="utf-8")


def test_ci_runs_the_disposable_docker_product_journey() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "docker-product-e2e:" in workflow
    assert "bash scripts/test-docker-e2e.sh" in workflow

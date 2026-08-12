from pathlib import Path


def test_env_is_injected_at_runtime_not_copied_into_image():
    root = Path(__file__).parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    copy_lines = [
        line for line in dockerfile.splitlines()
        if line.strip().upper().startswith("COPY ")
    ]
    assert all(".env" not in line for line in copy_lines)
    assert "env_file:" in compose
    assert "- .env" in compose

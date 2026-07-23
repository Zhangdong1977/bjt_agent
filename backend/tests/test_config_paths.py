from pathlib import Path

from backend.config import Settings


def test_rule_library_path_falls_back_from_linux_deploy_path():
    settings = Settings(
        rule_library_dir=Path("/home/openclaw/bjt_agent/docs/rules"),
        _env_file=None,
    )

    assert settings.rule_library_path == (settings.project_root / "docs" / "rules").resolve()


def test_rule_library_path_keeps_existing_custom_directory(tmp_path):
    settings = Settings(rule_library_dir=tmp_path, _env_file=None)

    assert settings.rule_library_path == tmp_path.resolve()


def test_duplicate_rule_library_path_falls_back_from_linux_deploy_path():
    settings = Settings(
        duplicate_rule_library_dir=Path(
            "/home/openclaw/bjt_agent/docs/rules-duplicate"
        ),
        _env_file=None,
    )

    assert settings.duplicate_rule_library_path == (
        settings.project_root / "docs" / "rules-duplicate"
    ).resolve()

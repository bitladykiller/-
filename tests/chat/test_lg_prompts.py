from pathlib import Path

import app.chat.infrastructure.modeling.prompts as prompts


class FakeLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> object:
        self.info_messages.append(msg)
        return None

    def warning(self, msg: str, *args: object, **kwargs: object) -> object:
        self.warning_messages.append(msg)
        return None


def test_default_prompts_expose_required_keys() -> None:
    assert "router_system" in prompts.DEFAULT_PROMPTS
    assert "general_query" in prompts.DEFAULT_PROMPTS
    assert "react_system" in prompts.DEFAULT_PROMPTS


def test_load_prompts_from_yaml_returns_empty_for_invalid_yaml_shape(
    tmp_path: Path,
) -> None:
    yaml_path = tmp_path / "lg_prompts.yaml"
    yaml_path.write_text("- item1\n- item2\n", encoding="utf-8")

    assert prompts.load_prompts_from_yaml(FakeLogger(), yaml_path) == {}


def test_load_prompts_from_yaml_reads_string_overrides(tmp_path: Path) -> None:
    yaml_path = tmp_path / "lg_prompts.yaml"
    yaml_path.write_text(
        "router_system: custom router\nreact_system: custom react\ncount: 3\n",
        encoding="utf-8",
    )

    assert prompts.load_prompts_from_yaml(FakeLogger(), yaml_path) == {
        "router_system": "custom router",
        "react_system": "custom react",
    }

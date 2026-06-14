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


def test_prompt_yaml_path_resolves_yaml_in_same_directory() -> None:
    assert prompts.prompt_yaml_path("/tmp/agent/lg_prompts.py") == Path("/tmp/agent/lg_prompts.yaml")


def test_normalize_prompt_overrides_filters_non_string_pairs() -> None:
    overrides = prompts._normalize_prompt_overrides(
        {
            "router_system": "router prompt",
            "general_query": 123,
            1: "ignored",
        }
    )

    assert overrides == {"router_system": "router prompt"}


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


def test_build_prompt_mapping_merges_overrides_on_top_of_defaults() -> None:
    prompt_mapping = prompts.build_prompt_mapping(
        {
            "router_system": "default router",
            "general_query": "default general",
        },
        {"router_system": "custom router"},
    )

    assert prompt_mapping["router_system"] == "custom router"
    assert prompt_mapping["general_query"] == "default general"

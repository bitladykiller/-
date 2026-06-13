from pathlib import Path

import importlib.util

_MODULE_PATH = Path(__file__).resolve().parents[1] / "llm_backend" / "run_support.py"
_SPEC = importlib.util.spec_from_file_location("run_support", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
run_support = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_support)


def test_switch_to_backend_dir_uses_string_path() -> None:
    calls: list[str] = []

    run_support.switch_to_backend_dir(
        Path("/tmp/project/llm_backend"),
        chdir=calls.append,
    )

    assert calls == ["/tmp/project/llm_backend"]


def test_build_uvicorn_run_kwargs_returns_dev_defaults() -> None:
    assert run_support.build_uvicorn_run_kwargs(host="0.0.0.0", port=8000) == {
        "host": "0.0.0.0",
        "port": 8000,
        "access_log": False,
        "log_level": "error",
        "reload": True,
    }


def test_start_dev_server_switches_directory_then_runs_uvicorn() -> None:
    calls: list[tuple[str, object]] = []

    def fake_chdir(path: str) -> None:
        calls.append(("chdir", path))

    def fake_uvicorn_run(app_import_path: str, **kwargs: object) -> None:
        calls.append(("uvicorn", app_import_path, kwargs))

    run_support.start_dev_server(
        app_import_path="main:app",
        backend_dir=Path("/tmp/project/llm_backend"),
        host="0.0.0.0",
        port=8000,
        uvicorn_run=fake_uvicorn_run,
        chdir=fake_chdir,
    )

    assert calls == [
        ("chdir", "/tmp/project/llm_backend"),
        (
            "uvicorn",
            "main:app",
            {
                "host": "0.0.0.0",
                "port": 8000,
                "access_log": False,
                "log_level": "error",
                "reload": True,
            },
        ),
    ]

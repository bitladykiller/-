import importlib.util
from pathlib import Path

import app.core as core_package
import app.core.config as config_module
import app.core.database as database_module
import app.core.logger as logger_module


def test_core_package_re_exports_stable_runtime_objects() -> None:
    assert core_package.ServiceType is config_module.ServiceType
    assert core_package.BusinessSettings is config_module.BusinessSettings
    assert core_package.InfrastructureSettings is config_module.InfrastructureSettings
    assert core_package.Settings is config_module.Settings
    assert core_package.settings is config_module.settings
    assert core_package.AsyncSessionLocal is database_module.AsyncSessionLocal
    assert core_package.Base is database_module.Base
    assert core_package.engine is database_module.engine
    assert core_package.setup_logging is logger_module.setup_logging
    assert core_package.get_logger is logger_module.get_logger
    assert core_package.format_log_context is logger_module.format_log_context
    assert core_package.__all__ == [
        "ServiceType",
        "BusinessSettings",
        "InfrastructureSettings",
        "Settings",
        "settings",
        "AsyncSessionLocal",
        "Base",
        "engine",
        "setup_logging",
        "get_logger",
        "format_log_context",
    ]


def test_backend_root_package_declares_no_stable_api_surface() -> None:
    module_path = Path(__file__).resolve().parents[2] / "llm_backend" / "__init__.py"
    spec = importlib.util.spec_from_file_location("llm_backend_root", module_path)
    assert spec is not None and spec.loader is not None
    backend_root = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend_root)

    assert backend_root.__all__ == []
    assert "后端根包" in (backend_root.__doc__ or "")

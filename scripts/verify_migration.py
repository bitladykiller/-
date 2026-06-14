#!/usr/bin/env python3
"""验证当前项目是否已经彻底收口到单一 `app/` 主代码树。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def check_primary_structure() -> dict[str, bool]:
    """检查当前主目录是否完整。"""
    return {
        "app/": (REPO_ROOT / "app").exists(),
        "app/api/": (REPO_ROOT / "app" / "api").exists(),
        "app/chat/": (REPO_ROOT / "app" / "chat").exists(),
        "app/knowledge/": (REPO_ROOT / "app" / "knowledge").exists(),
        "app/user/": (REPO_ROOT / "app" / "user").exists(),
        "app/shared/": (REPO_ROOT / "app" / "shared").exists(),
        "app/scripts/": (REPO_ROOT / "app" / "scripts").exists(),
        "docs/ARCHITECTURE.md": (REPO_ROOT / "docs" / "ARCHITECTURE.md").exists(),
        "docs/MIGRATION.md": (REPO_ROOT / "docs" / "MIGRATION.md").exists(),
        "docs/DEPLOYMENT.md": (REPO_ROOT / "docs" / "DEPLOYMENT.md").exists(),
    }


def check_removed_legacy_paths() -> dict[str, bool]:
    """检查旧兼容目录是否已移除。"""
    return {
        "app/lg_agent/": not (REPO_ROOT / "app" / "lg_agent").exists(),
        "app/memory/": not (REPO_ROOT / "app" / "memory").exists(),
        "app/services/": not (REPO_ROOT / "app" / "services").exists(),
        "llm_backend/": not (REPO_ROOT / "llm_backend").exists(),
    }


def count_legacy_imports(patterns: tuple[str, ...]) -> dict[str, int]:
    """统计仓库中残留旧导入模式的数量。"""
    results = {pattern: 0 for pattern in patterns}
    script_path = Path(__file__).resolve()
    for root in ("app", "tests", "examples", "scripts"):
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            if py_file.resolve() == script_path:
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for pattern in patterns:
                if pattern in content:
                    results[pattern] += 1
    return results


def main() -> int:
    """输出验证报告。"""
    print("=" * 60)
    print("结构验证报告")
    print("=" * 60)
    print()

    primary = check_primary_structure()
    removed = check_removed_legacy_paths()
    legacy_imports = count_legacy_imports(
        (
            "from app.lg_agent",
            "from app.memory",
            "from app.services",
            "from app.core",
            "from app.security",
            "from app.models",
            "app.chat.infrastructure.lg_",
            "app.knowledge.infrastructure.schemas",
        )
    )

    print("1. 主目录检查")
    print("-" * 40)
    primary_ok = True
    for name, exists in primary.items():
        status = "✓" if exists else "✗"
        print(f"  {status} {name}")
        primary_ok = primary_ok and exists
    print()

    print("2. 旧目录移除检查")
    print("-" * 40)
    removed_ok = True
    for name, removed_flag in removed.items():
        status = "✓" if removed_flag else "✗"
        print(f"  {status} {name}")
        removed_ok = removed_ok and removed_flag
    print()

    print("3. 旧导入检查")
    print("-" * 40)
    imports_ok = True
    for pattern, count in legacy_imports.items():
        status = "✓" if count == 0 else "✗"
        print(f"  {status} {pattern}: {count}")
        imports_ok = imports_ok and count == 0
    print()

    print("=" * 60)
    print("总结")
    print("=" * 60)
    if primary_ok and removed_ok and imports_ok:
        print("✓ 当前结构已彻底收口到 `app/` 主代码树。")
        return 0

    print("✗ 仍存在旧目录或旧导入，请继续清理。")
    return 1


if __name__ == "__main__":
    sys.exit(main())

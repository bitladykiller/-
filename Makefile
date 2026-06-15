.PHONY: help install test lint format clean build

help: ## 显示帮助信息
	@echo "可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## 安装生产依赖
	pip install -e .

install-dev: ## 安装开发依赖
	pip install -e ".[dev]"

test: ## 运行测试
	pytest tests/ -v

test-cov: ## 运行测试并生成覆盖率报告
	pytest tests/ --cov=app --cov-report=html --cov-report=term

lint: ## 代码检查
	ruff check app/ rag_doc_parser/ shared_retrieval/ scripts/ tests/

format: ## 格式化代码
	ruff format app/ rag_doc_parser/ shared_retrieval/ scripts/ tests/

type-check: ## 类型检查
	mypy app/ rag_doc_parser/ shared_retrieval/

clean: ## 清理构建产物
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf htmlcov/
	rm -rf .coverage

build: ## 构建 Docker 镜像
	docker compose build

docker-down: ## 停止 Docker 服务
	docker compose down

docker-logs: ## 查看 Docker 日志
	docker compose logs -f

pre-commit: ## 运行 pre-commit hooks
	pre-commit run --all-files

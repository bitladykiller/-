# 贡献指南

感谢您考虑为 deepseek-agent 项目做出贡献！

## 开发环境设置

### 1. 克隆仓库

```bash
git clone https://github.com/yourusername/deepseek-agent.git
cd deepseek-agent
```

### 2. 安装依赖

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 或使用 Makefile
make install-dev
```

### 3. 配置环境变量

```bash
cp .env.example app/.env
# 编辑 app/.env 配置必要的环境变量
```

## 代码规范

### 代码风格

- 使用 `ruff` 进行代码格式化和检查
- 最大行长度: 100 字符
- 使用类型注解 (Python 3.10+)

运行代码检查:

```bash
make lint
make format
```

### 类型检查

使用 `mypy` 进行类型检查:

```bash
make type-check
```

### 测试

运行测试:

```bash
make test
```

生成覆盖率报告:

```bash
make test-cov
```

## Git 工作流

### 分支命名

- `feature/*`: 新功能
- `bugfix/*`: Bug 修复
- `refactor/*`: 重构
- `docs/*`: 文档更新

### 提交信息格式

遵循以下格式:

```
<type>: <subject>

<body>

<footer>
```

类型:
- `feat`: 新功能
- `fix`: Bug 修复
- `refactor`: 重构
- `docs`: 文档
- `test`: 测试
- `chore`: 构建/工具链

示例:

```
feat: 添加新的记忆提取功能

- 支持多轮对话记忆提取
- 优化 LLM prompt 设计

Closes #123
```

### Pull Request 流程

1. Fork 仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交改动 (`git commit -m 'feat: 添加某功能'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 代码审查标准

### 必须满足

- ✅ 通过所有测试
- ✅ 通过代码检查 (ruff)
- ✅ 通过类型检查 (mypy)
- ✅ 代码覆盖率不低于 80%
- ✅ 有清晰的文档和注释
- ✅ 遵循项目架构规范

### 推荐实践

- 编写单元测试覆盖新功能
- 更新相关文档
- 保持代码简洁易读
- 遵循单一职责原则

## 架构规范

### 模块分层

```
app/
├── api/        # HTTP API 层
├── core/       # 核心基础设施 (配置、数据库、日志)
├── models/     # 数据模型
├── services/   # 业务服务层
├── lg_agent/   # LangGraph Agent 模块
└── memory/     # 记忆系统
```

### 命名规范

- 模块名: 小写下划线 (`memory_extractor.py`)
- 类名: 大驼峰 (`MemoryExtractor`)
- 函数名: 小写下划线 (`extract_memory`)
- 常量: 大写下划线 (`MAX_MEMORY_SIZE`)

## 问题报告

使用 GitHub Issues 报告问题:

- 清晰描述问题
- 提供复现步骤
- 标注优先级和类型

## 许可证

本项目采用 MIT 许可证。贡献的代码将采用相同许可证。

## 联系方式

- GitHub Issues: 提问和 Bug 报告
- Email: your.email@example.com

感谢您的贡献！

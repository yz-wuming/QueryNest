# Contributing to QueryNest

感谢你愿意为 QueryNest 贡献。请阅读以下约定，保证工作流一致、评审顺畅。

## Development Setup

- Python `>=3.10`，建议使用虚拟环境（`python -m venv .venv`）。
- 安装核心依赖：
  ```bash
  pip install -e ".[dev]"
  ```
  可选能力按需安装（如 `.[paddleocr]`、`.[markdown]`、`.[api]`）——详见 `pyproject.toml`。
- 前端为服务端内嵌的静态页面（`querynest/api/static/index.html`），无需单独构建。
- 本机开发连模型需要 `.env`（参考 `.env.example`），其中的 `QUERYNEST_LLM_API_KEY` 为必填。

## Testing

- 后端单元测试（必须全绿）：
  ```bash
  python -m compileall querynest
  python -m pytest tests -q
  ```
- 前端 E2E（需先启动服务）：
  ```bash
  python -m querynest serve   # 或 querynest serve，默认 http://127.0.0.1:9623
  node scripts/e2e.mjs
  ```
  E2E 需要本机可用的 `playwright` 及浏览器。
- 真实 RAG / VLM / Provider 相关验证依赖真实外部服务与 Key；缺少 Key 的用例如实标注 `NOT TESTED`，不得伪造结果。
- 新增代码请附带相应测试（单元测试或 E2E 断言）。

## Pull Request

- 从新分支提交，PR 标题简洁描述改动。
- 分支通过前必须：
  - `pytest tests -q` 全绿；
  - E2E 通过（如改动涉及前端）；
  - `git diff --check` 无 whitespace / 冲突标记；
  - 无 `raganything` 旧品牌残留、无明文 Secret。
- 保持改动聚焦：不要混入无关重构或超范围依赖引入。
- 说明改动动机与验证方式（`what` + `why`）。

## Code Style

- Python 遵循 `pyproject.toml` 中配置的 `black`、`isort`、`flake8`（dev 依赖内）。
- 命名清晰、避免无注释赘余；仅在“为什么”不显然处写注释。
- 前端沿用现有 Design Token 与组件约定，不做大规模视觉重构。

## Issues

- Bug：附复现步骤、期望行为、实际行为、相关日志与版本。
- 功能请求：说明使用场景与想要解决的问题。
- 安全相关问题请走 `SECURITY.md` 的私有披露流程，不要公开贴出真实 Key / 凭证。

## 其他

- 不要修改 Git 历史 / force push。
- 不提交 `.env`、`querynest_storage*`、`querynest_output*`、`*.log`、`secrets.json` 等被忽略的真实数据与密钥。
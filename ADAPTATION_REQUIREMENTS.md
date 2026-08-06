# ADAPTATION_REQUIREMENTS.md — SEAM 适配要求

> 本文件是 SEAM 仓库根目录的适配要求声明。`README.md` L52 承诺该文件会被自动加载，
> 也可通过 `--user-constraints <file>` 显式传入（见 `src/scripts/sm_adapt_cli.py` 与
> `run_e2e*.sh` 的 `--user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md` 默认值）。
> 以下条目全部来自仓库现状（README / src/README / 脚本 / 文档 / 代码），不包含虚构能力。

## 1. 运行平台要求

- 生产运行目标仅为 **Linux**，要求 **Python 3.10+**（`README.md` L39、`docs/User_Guide.md` L8；
  `src/tests/test_documented_cli_contracts.py` 对该契约有断言）。
- 强制 CI 使用无硬件 Linux runner；真实 NPU/GPU 集成验证保持可选，不作为发布门禁。

## 2. 运行时依赖

- base 依赖包含 `typing_extensions`（`docs/User_Guide.md` L10）。
- 可选 extra：
  - `[sqlite]`（`pysqlite3-binary`）：SQLite completion evidence；不安装只会禁用该 evidence。
  - `[dashboard]`：可选实时仪表盘渲染器（textual 优先，rich 回退）。
- 运行前需要已启动的 OpenCode Server，默认地址 `http://127.0.0.1:4098`
  （`README.md` L48；端口不同时用 `--server_url` 指定）。

## 3. 资源布局要求（代码按路径读取）

仓库 `src/` 下的资源按约定布局，运行时由各 loader 按路径加载：

- `src/workflows/`：YAML 状态机工作流（默认入口 `seam_auto_default.yaml`）。
- `src/prompts/`：各阶段 LLM prompt（含 Phase-3 entry-script 提示词）。
- `src/config/`：framework 默认配置。
- `src/schemas/`：报告等 JSON schema（如 `phase_6_reports.json`）。
- `src/test_project_template/`：迁移项目模板（`train.py` / `requirements.txt` / `README.md`）。

wheel 打包需把这些资源目录作为 package-data 纳入安装产物（issue #10 工作轨，进行中）。

## 4. 目标项目形态契约（SEAM 迁移的输入）

可迁移的 CUDA 项目建议包含（`src/README.md` L35-41 布局图）：

- `ADAPTATION_REQUIREMENTS.md`：项目自身的适配约束。`run_e2e*.sh` 检测其存在，
  默认以 `--user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md` 传入。
- `original_src/`：原始代码目录。
- `test_data_and_scripts/`：Phase-3 入口脚本目录；`run_e2e*.sh` 会发现其中的 `*.py`
  作为入口脚本候选。本仓库在 `src/test_data_and_scripts/` 提供
  `run_inference.py` 与 `run_e2e.py` 两个参考实现。

### cwd 契约（入口脚本执行目录）

`RepairLoopEngine._resolve_script_cwd`（`src/core/repair_loop.py` L1139-1147）规定：
入口脚本以 `cd <项目根> && python test_data_and_scripts/run_inference.py` 形式执行时，
cd 剥离后的 argv 为 `[python, test_data_and_scripts/run_inference.py]`，执行 cwd 固定为
项目根目录，使相对脚本路径正确解析，避免出现
`test_data_and_scripts/test_data_and_scripts/run_inference.py` 双重嵌套。

## 5. 已知限制（如实记录）

- **约束注入**：`ADAPTATION_REQUIREMENTS.md` 会被启动器检测并以 `--user-constraints`
  传入，但据 `src/docs/improvement_plan.md` L17，其内容目前尚未注入任何 LLM prompt
  （规划中的改进项）。
- **seam CLI 入口**：`[project.scripts]` 尚未在 `src/pyproject.toml` 声明
  （`scripts/sm_adapt_cli.py` 已定义可调用的 `main()`）；属 issue #10 工作轨。
- **报告 schema 版本化**：`src/schemas/phase_6_reports.json` 当前未声明 `version`
  字段；属 issue #18 工作轨。

## 6. 验证与回归基线

- 文档契约套件：`src/tests/test_documented_cli_contracts.py` +
  `src/tests/test_usage_guide_docs.py` = 32 passed / 2 skipped / 0 failed。
- 文档缺失项契约：`src/tests/test_documentation_contract_red.py` = 5/5
  （本文件、`src/test_data_and_scripts/`、`run_inference.py`、`run_e2e.py` 全部存在）。

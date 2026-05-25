# NPU 迁移框架改进方案：Phase 3 入口脚本增强与 OOM 防护

## 1. Phase 3 Prompt 增强：非交互式要求

### 问题描述
目前的 Phase 3 Prompt 仅要求提供运行命令，未明确限制脚本的交互性。导致 LLM 选择了包含 `while True: input()` 的交互式命令行界面（CLI）脚本，使得 Phase 5 验证阶段挂起或无法自动化运行。

### 修改方案
在 `migration_utils/prompts/phase_3_entry_script.md` 中增加 **"Headless Execution Compliance"** 章节。

### Prompt 追加内容
```markdown
## Mandatory: Headless Execution Compliance
The script you select or create MUST be capable of running in a **Headless (Non-Interactive)** environment for automated validation.

### Requirements for a Valid Run Command:
1. **No Interactive Loops**: The script MUST NOT wait for user input (e.g., `input()`, `raw_input()`, or infinite `while` loops waiting for terminal input).
2. **Automatic Exit**: The script MUST complete its task and exit with Code 0 automatically. It cannot be a REPL or a persistent CLI that waits for user commands.
3. **Argument Overrides**: If the chosen script is interactive by default (e.g., `demo_cli.py`), you MUST provide a non-interactive wrapper command or modify the script to bypass the interactive mode (e.g., pass a specific flag like `--non-interactive` or `--auto`).

### Analysis Step:
Before returning the JSON, analyze the script logic.
- If `input()` exists in the main execution flow -> **INVALID**.
- If the script creates a Web UI (Gradio/Streamlit) that blocks the main thread -> **INVALID** (unless it can be run in a fire-and-forget way).

## Output Format
(Return standard JSON with `entry_script_path` and `run_command`)
```

---

## 2. Phase 3.5: 入口脚本静态合规性检查

### 阶段定义
*   **阶段名称**：`phase_35_static_entry_validation`
*   **阶段性质**：独立的校验阶段（Static Validation），位于 Phase 3 之后、Phase 4 之前。
*   **会话复用**：复用 Phase 3 生成的 `session_id`（保持上下文连续性，无需重新加载项目）。

### 核心流程
1. **触发**：Phase 3 成功输出 `entry_script_path` 后，框架自动进入 Phase 3.5。
2. **分析**：Phase 3.5 Prompt 指示 LLM（使用同个 Session）读取 Phase 3 选定的脚本代码。
3. **静态审查 (Static Check)**：
    *   检查是否存在 `input()`, `sys.stdin.read()` 等阻塞调用。
    *   检查是否存在 `while True` 且无退出条件的死循环。
    *   检查是否依赖未安装的外部二进制文件。
4. **判定与输出 (JSON)**：
    *   **Pass**: `{"validation_passed": true, "reasoning": "..."}`
    *   **Fail**: `{"validation_passed": false, "issues": ["Found `input()` on line 78", ...], "suggestion": "..."}`

### Prompt 设计 (`prompts/phase_35_static_validate.md`)
```markdown
# Phase 3.5 - Entry Script Static Compliance Check

You are performing a static analysis on the entry script selected in Phase 3.
**Do NOT run the script.** Just read the code file.

## Context
Project Directory: `{project_dir}`
Selected Entry Script: `{entry_script_path}` (from Phase 3)

## Validation Checklist
1. **Non-Interactive**: Scan for `input()`, `getpass`, or blocking terminal reads.
2. **Clean Exit**: Ensure the script does not run indefinitely (e.g., infinite loops without breaks).
3. **Headless Ready**: Ensure the script does not spawn blocking UI windows (like `cv2.imshow`) without a flag to disable them.

## Output
Return a JSON object:
{
  "validation_passed": boolean,
  "issues": ["List specific issues found, e.g., 'Line 45 calls input()'"],
  "fix_plan": "If failed, describe how to fix the entry script (e.g., 'Remove the loop at line 45')"
}
```

### Validator 设计 (`validators/validate_entry_static.py`)
```python
def validate(data: dict[str, object]) -> ValidationDict:
    passed = data.get("validation_passed")
    if not isinstance(passed, bool):
        return {"passed": False, "errors": ["Missing validation_passed boolean"]}
    
    if not passed:
        # If LLM says validation failed, we treat this Phase as Failed
        # to trigger the loop back to Phase 3.
        issues = data.get("issues", [])
        return {"passed": False, "errors": [str(i) for i in issues]}
        
    return {"passed": True, "errors": [], "warnings": []}
```

### 工作流 (Workflow) 状态机改造
在 `npu_migration_v1.yaml` 中增加：
```yaml
  - id: phase_35_static_entry_validation
    name: Static Entry Validation
    timeout: 600
    prompt_template: "migration_utils/prompts/phase_35_static_validate.md"
    output_schema: {} # Dynamic JSON structure
    validator: entry_static
    transitions:
      # If LLM finds issues (passed=false), we loop back to Phase 3 to fix it.
      on_failure: phase_3 
      on_success: phase_4
```
*注意：状态机需支持从 `phase_35` `on_failure` 返回 `phase_3`，并在调用 Phase 3 时携带 Phase 3.5 的失败原因。*

---

## 3. Issue 3: OOM 防护与临时文件重定向

### 修改方案
在 `migration_utils/core/repair_loop.py` 中，修改 `subprocess.run` 调用方式，由内存捕获改为文件重定向。

### 代码实现细节

#### A. 创建临时文件
在执行循环外部创建文件，循环内部复用，避免重复创建：
```python
import tempfile
import shutil

# 在 run() 开始时
with tempfile.TemporaryDirectory(prefix="sm_adapt_run_") as tmp_dir:
    out_file = os.path.join(tmp_dir, "stdout.log")
    err_file = os.path.join(tmp_dir, "stderr.log")
    
    # 进入 Iteration Loop
    for iteration in ...:
        # ...
        with open(out_file, "w") as f_out, open(err_file, "w") as f_err:
            completed = subprocess.run(
                cmd_argv,
                # capture_output=True, # 删除此行
                stdout=f_out,          # 新增：重定向
                stderr=f_err,          # 新增：重定向
                text=True,
                # ...
            )
```

#### B. 安全读取日志 (Truncated Read)
为了不让父进程 OOM，不能读取整个文件。需要实现一个 `read_tail(filepath, size_bytes)`。

```python
def _read_tail(filepath: str, max_bytes: int = 500000) -> str:
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return ""
    with open(filepath, "r", errors="ignore") as f:
        # 简单实现：跳过前面的内容读最后部分，或者直接用 seek
        # 为了处理多字节字符截断导致的 decode error，简单读取最后一段即可
        try:
            f.seek(0, 2) # Seek to end
            file_size = f.tell()
            if file_size <= max_bytes:
                f.seek(0)
                return f.read()
            else:
                f.seek(file_size - max_bytes)
                # Skip partial first line
                f.readline() 
                return f.read()
        except:
            return "" # Fallback
```

#### C. Error Analyzer 输入更新
在 `repair_loop.py` 中，替换原来 `completed.stdout` 的使用位置：
```python
# 原逻辑
# error_text = self._combine_error(completed.stdout, completed.stderr)

# 新逻辑
final_stdout = _read_tail(out_file)
final_stderr = _read_tail(err_file)
error_text = self._combine_error(final_stdout, final_stderr)
```

### 优势
1.  **子进程安全**：子进程可以任意输出日志，不会受限于管道缓冲区大小。
2.  **父进程安全**：父进程只读取日志文件尾部（如 500KB），即使日志文件 10GB，父进程内存占用依然极低。
3.  **完整性**：如果报错刚好在日志的前半部分？通常程序的致命错误（如 OOM, CUDA error）会出现在最后。如果担心遗漏，可以配置为读取 `First 10KB + Last 490KB`。

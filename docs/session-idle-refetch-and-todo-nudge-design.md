# Session Idle 重取与 TODO 非空追问设计方案（最终可实施版）

> 本文件为最终版。已纳入对 6 个评审问题的最终决定，可直接据此实施。

## 1. 背景与目标

### 1.1 当前实现概览

OpenCode 会话交互集中在 `src/harness/session/manager.py` 的 `MigrationSessionManager`。
关键路径：

- `send_command()` (`src/harness/session/manager.py:344`)：对外入口，带重试与异常归类。
- `_send_message_raw()` (`src/harness/session/manager.py:1096`)：核心实现，单次消息往返。
- `wait_for_idle()` (`src/harness/session/manager.py:949`)：轮询 `/session/status` + TODO 信号 + SQLite 兜底，返回 `bool`。
- `_session_has_incomplete_todos()` (`src/harness/session/manager.py:927`)：基于 `/session/{id}/message?limit=20` 与 SQLite 推断 TODO。
- `_todo_signal_from_payload()` (`src/harness/session/manager.py:555`)：把任意 payload 归约为 `True`(未完成)/`False`(已完成)/`None`(无信号)。
- `_last_message_text_tolerant()` (`src/harness/session/manager.py:1058`)：容错读取最新一条消息。
- `_recover_empty_response_text()` (`src/harness/session/manager.py:1151`)：仅在 POST 返回空文本时，等待 idle 后重取最新消息。

### 1.2 已确认的现有缺陷（逐行核对）

**缺陷 1（目标 1）真实存在。** `_send_message_raw()` (`src/harness/session/manager.py:1135-1149`)：POST 非空时取 `text`，`wait_for_idle()` 通过后直接 `return text`，不重取。若 OpenCode 在 POST 返回后继续生成，idle 后产生的最新文本不会被采用。

**缺陷 2（目标 2）真实存在。** `wait_for_idle()` (`src/harness/session/manager.py:979`) 对 TODO 非空只 `sleep` 轮询到超时抛 `TimeoutError`，没有“停止生成但 TODO 非空”稳定态的主动干预。

### 1.3 目标

**目标 1：idle 后强制重取最新响应**

- `wait_for_idle()` 判定 idle 后，重新 `GET` 最新 assistant 消息文本作为返回值。
- idle 状态下“历史最新消息”即最终答案：若与 POST 文本一致，说明 POST 当时已是终态，行为等价；若不同，则采用更完整的最新消息（修复缺陷 1）。
- 仅保留“防污染”兜底（空 / prompt 回显 / compaction）；命中兜底时回退到“最近一次 POST 自身的有效响应文本”。

**目标 2：停止生成 + TODO 非空 的追问机制**

- 当观察到“非 running 且 TODO 明确未完成”稳定态时：
  1. 等待 10s（可配置）。
  2. 重新检查状态。
  3. 若仍是“非 running 且 TODO 明确未完成”，向 session 发送一段**追问 nudge 消息**。
- 若 10s 后 session 回到 running，则**继续等待**，不发送 nudge（见 §4.4）。
- nudge 语义：以原 prompt 为准确认是否完成；完成则清空 TODO 并按原格式返回；未完成则只做原 prompt 范围内的剩余工作；TODO 中存在 prompt 范围外条目立即清除。
- nudge 后重新进入收敛等待；nudge 有最大次数上限，全程受同一 `deadline` 约束。

## 2. 设计原则

- **不破坏既有语义**：POST 即终态时，新逻辑结果与旧逻辑等价。
- **保守优先**：running 中绝不返回；只有非 running 且 TODO 收敛（空或 nudge 后收敛）才返回。
- **复用现有判定**：TODO 判定、文本提取、防污染、SQLite 兜底全部复用。
- **泛化**：nudge 文案与阈值不绑定具体平台/workflow，不引入硬编码 case 规则。
- **可观测**：重取/nudge 关键节点打日志（仅状态与长度，不打正文）。
- **默认全开**：nudge 默认对所有 `send_command` 调用启用；提供参数仅用于覆盖/测试。

## 3. 评审结论（最终决定）

### 决定 1：idle 后直接采用重取的最新消息，不做“时间戳加严”

idle 状态下，`GET /session/{id}/message` 的最新 assistant 消息按定义就是最终答案：

- POST 即终态 → 重取 == POST 文本 → 替换与否结果一致。
- POST 为中间态 → 重取为更完整文本 → 正是要修复的缺陷 1。

因此**主行为是“重取并使用最新消息”**，无需比较时间戳/消息 ID。仅在重取命中防污染兜底（空 / prompt 回显 / compaction）时，回退到“最近一次 POST 自身的有效响应文本”。

> 现有测试 `test_pending_word_in_normal_text`（`src/tests/test_session_manager_guard.py:424`）让 POST 文本与历史最新消息不一致，是为单独验证 TODO 检测而构造的 artifact，不代表真实 OpenCode 行为。该测试需更新为“历史最新消息与 POST 文本一致”（仍保留含 "pending"/"resolved" 以验证其原意），详见 §6。

### 决定 2：`wait_for_idle()` 旧语义保持不变，实现时保持时间调用节奏

`wait_for_idle()` 仍返回 `bool`，TODO 非空时仍“等到超时返回 `False`”。新增的细粒度状态机仅供目标 2 的新路径使用。实现约束：旧语义路径的循环结构与 `time.time()` 调用次数/节奏需与现状一致，确保 `test_wait_for_idle_*`（`:433`、`:468`、`:502`）依赖的有限时间序列不被打乱。

### 决定 3：fallback 永远指向“最近一次 POST 自身的有效响应”，无效即报错

确认当前空响应路径行为 `_recover_empty_response_text()` (`src/harness/session/manager.py:1151-1170`)：重取为空/等于 previous/等于 command 时返回 `""`，最终在 `_send_message_raw()` 抛 `RuntimeError("Empty session response")`（`:1140-1141`）。即**当前对无效重取就是报错，不返回旧内容**。

最终规则统一为：

- 普通路径：fallback = 本次 POST 自身的有效响应文本（安全，本就是完整回复）。
- nudge 路径：fallback = **nudge 那次 POST 自身的响应文本**，绝不回退到最初的中间态 `initial_text`。
- 若 fallback 本身也无效 → 按空响应路径**报错**（与 A 失败处理一致）。

### 决定 4：每次 nudge 前刷新 `previous_text`，已确认安全

确认 `previous_text` 仅在 `_recover_empty_response_text()`（`src/harness/session/manager.py:1166`）用于“重取是否等于发送前旧消息”的判断，无其它用途、无跨模块引用。因此每次 nudge 前将其更新为“当前最新消息”是安全的。

### 决定 5：nudge 默认全开，仅留参数用于覆盖/测试

所有 `send_command` 调用方（selector / classifier / repair_loop / phase_runner 等）默认启用 nudge，无需任何改动、不显式传关闭。新增的开关/阈值参数默认值即“启用 + 10s + 2 次”，仅供将来或测试覆盖（如测试设 0 等待避免慢测试）。

### 决定 6：不为小 timeout 特判；超时即走原 `TimeoutError` 路线

等待 + 10s 窗 + nudge 全部在同一 `deadline` 内。若 nudge 导致用尽预算，按现状抛 `TimeoutError`，由 `send_command()`（`src/harness/session/manager.py:368`）捕获返回 `{"ok": false, ...}`。不引入新的超时分支。这是 timeout 配置问题，非本方案逻辑问题。

## 4. 详细设计

### 4.1 新增常量

在常量区（`src/harness/session/manager.py:32` 附近）新增：

```python
DEFAULT_TODO_STABILIZE_WAIT_S = 10.0   # 停止生成但 TODO 非空 → 二次确认前等待
DEFAULT_MAX_TODO_NUDGES = 2            # 同一轮请求最多追问次数
DEFAULT_TODO_NUDGE_ENABLED = True      # 默认启用追问
```

### 4.2 idle 等待结果结构化

```python
from enum import Enum

class IdleOutcome(str, Enum):
    IDLE = "idle"                  # 非 running 且 TODO 非未完成态
    TODO_PENDING = "todo_pending"  # 非 running 但 TODO 明确未完成（仅 _todo==True）
    TIMEOUT = "timeout"            # 超时仍未收敛
    RUNNING = "running"            # 退出时仍 running
```

新增内部方法（承载现有 `wait_for_idle` 全部判定逻辑）：

```python
def _await_idle_state(
    self,
    session_id: str,
    timeout_s: float,
    interval_s: float,
    return_on_todo_pending: bool,
) -> IdleOutcome:
    started = time.time()
    while time.time() - started < timeout_s:
        status = self._http("GET", "/session/status")
        if not status.get("ok"):
            # 复用现有 401/403/HARD_HTTP 处理与 SQLite 兜底逻辑
            ... (保持与现 wait_for_idle 一致)
        token = self._extract_status_token(status.get("data"), session_id)
        if token in RUNNING_TOKENS:
            time.sleep(interval_s)
            continue
        todo_state = self._session_has_incomplete_todos(session_id)
        if todo_state is True:
            if return_on_todo_pending:
                return IdleOutcome.TODO_PENDING
            time.sleep(interval_s)   # 旧语义：继续等到超时
            continue
        if token or todo_state is False:
            return IdleOutcome.IDLE
        sqlite_state = self._session_completion_from_sqlite(session_id)
        if sqlite_state is True:
            time.sleep(interval_s)
            continue
        return IdleOutcome.IDLE      # 无任何 running/未完成信号 → idle
    return IdleOutcome.TIMEOUT
```

`wait_for_idle()` 改为薄委托（保持 `bool` 语义与时间节奏，满足决定 2）：

```python
def wait_for_idle(self, session_id, timeout_s=300, interval_s=2.0) -> bool:
    outcome = self._await_idle_state(
        session_id,
        timeout_s=self._effective_wait_timeout(timeout_s),
        interval_s=interval_s,
        return_on_todo_pending=False,   # 旧语义：TODO 非空等到超时
    )
    return outcome == IdleOutcome.IDLE
```

> 注意：`_await_idle_state` 在 `return_on_todo_pending=False` 时，其循环结构、`time.time()`/`time.sleep()` 调用顺序必须与现有 `wait_for_idle()` 完全一致。

### 4.3 idle 后重取最新响应（目标 1 / 决定 1、3）

抽出防污染校验：

```python
def _is_usable_refetched_text(self, candidate, previous_text, command_text) -> bool:
    stripped = candidate.strip()
    if not stripped:
        return False
    if stripped == previous_text.strip():
        return False
    if stripped == command_text.strip():
        return False
    return True
```

idle 后重取（命中兜底回退到传入 fallback）：

```python
def _refetch_final_text(
    self,
    session_id: str,
    fallback_text: str,
    previous_text: str,
    command_text: str,
) -> str:
    latest = self._last_message_text_tolerant(session_id)
    if self._is_usable_refetched_text(latest, previous_text, command_text):
        wrapped = {"parts": [{"type": "text", "text": latest}]}
        if not self._is_compaction_payload(wrapped):
            return latest
    return fallback_text
```

> `fallback_text` 由调用方传入：普通路径传本次 POST 文本；nudge 路径传 nudge POST 文本（决定 3）。

让 `_recover_empty_response_text()` 复用 `_is_usable_refetched_text()`，去除重复判断（行为不变）。

### 4.4 收敛与追问主控（目标 1 + 目标 2）

```python
def _await_and_finalize(
    self,
    session_id: str,
    post_text: str,        # 最近一次 POST 自身响应文本（fallback 来源）
    previous_text: str,
    command_text: str,
    agent: str,
    timeout: int | float | None,
) -> str:
    effective_timeout = self._effective_wait_timeout(timeout)
    deadline = time.time() + effective_timeout
    nudge_count = 0
    current_post_text = post_text
    current_previous = previous_text

    while True:
        remaining = max(1.0, deadline - time.time())
        outcome = self._await_idle_state(
            session_id, timeout_s=remaining, interval_s=1.0,
            return_on_todo_pending=True,
        )

        if outcome == IdleOutcome.IDLE:
            return self._refetch_final_text(
                session_id, current_post_text, current_previous, command_text
            )
        if outcome in (IdleOutcome.TIMEOUT, IdleOutcome.RUNNING):
            raise TimeoutError("Session still running or has incomplete todos")

        # outcome == TODO_PENDING
        if not self._todo_nudge_enabled or nudge_count >= self._max_todo_nudges:
            raise TimeoutError("Session stopped with incomplete todos after nudges")

        # 等待 10s 后二次确认（盲等；10s 后若回到 running 由下方分支处理）
        time.sleep(self._todo_stabilize_wait_s)
        recheck = self._await_idle_state(
            session_id, timeout_s=max(1.0, deadline - time.time()),
            interval_s=1.0, return_on_todo_pending=True,
        )
        if recheck == IdleOutcome.IDLE:
            return self._refetch_final_text(
                session_id, current_post_text, current_previous, command_text
            )
        if recheck != IdleOutcome.TODO_PENDING:
            # running / timeout：回主循环，按剩余 deadline 继续等待（决定 6 / 用户语义）
            continue

        # 仍是“停止生成 + TODO 非空”：刷新 previous 基线后发送 nudge
        current_previous = self._last_message_text_tolerant(session_id) or current_previous
        nudge_count += 1
        nudge_text = self._send_todo_nudge(session_id, agent=agent, timeout=timeout)
        if nudge_text:
            current_post_text = nudge_text   # fallback 切换到 nudge 响应（决定 3）
        # 回主循环，等待 nudge 收敛
```

要点：

- 每个 IDLE 出口都调用 `_refetch_final_text`（目标 1 在首次与 nudge 后均生效）。
- nudge 仅在“10s 后仍 TODO_PENDING”时触发；回到 running 则继续等待（覆盖用户场景）。
- 每次 nudge 前刷新 `current_previous`（决定 4）。
- nudge 后 fallback 切到 nudge 响应文本（决定 3）。
- 全程受同一 `deadline` 约束；超时走 `TimeoutError`（决定 6）。

### 4.5 追问 nudge 发送（不递归）

从 `_send_message_raw()` 抽出“只发 POST + 基础错误归类 + 返回本次响应文本”的薄封装，供 nudge 复用，避免递归进入 `_await_and_finalize`：

```python
def _post_message_only(
    self, session_id: str, text: str, agent: str, timeout: int | float | None,
) -> str:
    payload = {"parts": [{"type": "text", "text": text}]}
    if agent:
        payload["agent"] = agent
    http_timeout = self._effective_wait_timeout(timeout) + 30
    resp = self._http("POST", f"/session/{session_id}/message", body=payload, timeout=http_timeout)
    # 复用 _send_message_raw 中相同的 401/403/5xx/transport 归类
    ... (raise SessionAuthError / SessionServerError / SessionTransportError)
    data = resp.get("data") or {}
    if isinstance(data, dict):
        info = data.get("info") or {}
        if isinstance(info, dict) and info.get("error"):
            raise RuntimeError(self._extract_error_text(info.get("error")))
        if self._is_compaction_payload(data):
            raise SessionCompacted("Compaction response is incomplete")
    return self._extract_message_text(data)

def _send_todo_nudge(self, session_id: str, agent: str, timeout) -> str:
    nudge = self._build_todo_nudge_prompt()
    logger.info("[TODO NUDGE] session=%s", session_id)
    return self._post_message_only(session_id, nudge, agent=agent, timeout=timeout)
```

> `_send_message_raw()` 与 `_post_message_only()` 共享同一段 HTTP 错误归类逻辑，建议抽成一个私有 helper（如 `_classify_post_failure(resp, session_id)`）供两者调用，避免重复。

### 4.6 nudge 文案模板

```python
def _build_todo_nudge_prompt(self) -> str:
    return (
        "System check: Your previous turn stopped, but the session still has an "
        "open TODO list. Do not start any new work beyond the original request.\n\n"
        "Please do the following now:\n"
        "1. Determine whether you have fully completed everything the ORIGINAL "
        "prompt required (judge by the original prompt, not by the TODO list).\n"
        "2. If the TODO list contains any item that was NOT required by the "
        "original prompt, remove it immediately and do not act on it.\n"
        "3. If the original task is already complete: clear the TODO list and "
        "return the final result strictly in the format the original prompt "
        "requested.\n"
        "4. If the original task is not complete: finish only the remaining work "
        "the original prompt requires, then clear the TODO list and return the "
        "result in the requested format.\n\n"
        "Return only the result required by the original prompt. Do not add extra "
        "tasks, extra files, or extra explanations."
    )
```

### 4.7 改写 `_send_message_raw()` 非空分支

```python
text = self._extract_message_text(data)
if not text:
    text = self._recover_empty_response_text(
        session_id, timeout, previous_text, command_text=command_text
    )
    if not text:
        raise RuntimeError("Empty session response")
    return text

return self._await_and_finalize(
    session_id=session_id,
    post_text=text,
    previous_text=previous_text,
    command_text=command_text,
    agent=agent,
    timeout=timeout,
)
```

空响应路径保持不变（仍走 `_recover_empty_response_text`）。

### 4.8 配置接入

`__init__` (`src/harness/session/manager.py:126`) 增参（默认值即生产值，决定 5）：

```python
def __init__(self, ..., 
             todo_nudge_enabled: bool = DEFAULT_TODO_NUDGE_ENABLED,
             todo_stabilize_wait_s: float = DEFAULT_TODO_STABILIZE_WAIT_S,
             max_todo_nudges: int = DEFAULT_MAX_TODO_NUDGES) -> None:
    ...
    self._todo_nudge_enabled = bool(todo_nudge_enabled)
    self._todo_stabilize_wait_s = max(0.0, float(todo_stabilize_wait_s))
    self._max_todo_nudges = max(0, int(max_todo_nudges))
```

可选环境变量覆盖（遵循现有 env 风格）：`SEAM_TODO_NUDGE_ENABLED` / `SEAM_TODO_STABILIZE_WAIT_S` / `SEAM_MAX_TODO_NUDGES`。

`send_command()` / `send_json_command()` 签名不变，调用方零改动（决定 5）。

### 4.9 日志

- idle 后重取且文本变化：`logger.info("[REFETCH] session=%s replaced=%s len=%d", ...)`。
- 进入 TODO_PENDING：`logger.info`，记录 session 与轮次。
- 发送 nudge：`logger.info("[TODO NUDGE] ...")`。
- nudge 超限放弃：`logger.warning`。
- 不打印 prompt/响应正文，仅状态与长度。

## 5. 改动清单

### 5.1 源码 `src/harness/session/manager.py`

- 新增常量：`DEFAULT_TODO_STABILIZE_WAIT_S` / `DEFAULT_MAX_TODO_NUDGES` / `DEFAULT_TODO_NUDGE_ENABLED`。
- 新增 `IdleOutcome` 枚举。
- 新增 `_await_idle_state(...)`；`wait_for_idle()` 改薄委托（保持时间节奏）。
- 新增 `_is_usable_refetched_text(...)`、`_refetch_final_text(...)`。
- 新增 `_await_and_finalize(...)`、`_post_message_only(...)`、`_send_todo_nudge(...)`、`_build_todo_nudge_prompt(...)`。
- 抽 `_classify_post_failure(...)` 供 `_send_message_raw` 与 `_post_message_only` 共用。
- 改写 `_send_message_raw()` 非空分支调用 `_await_and_finalize`。
- `_recover_empty_response_text()` 复用 `_is_usable_refetched_text`。
- `__init__` 增 3 个配置参数。

### 5.2 测试 `src/tests/test_session_manager_guard.py`

- 更新 `test_pending_word_in_normal_text`（`:424`）：历史最新消息改为与 POST 文本一致（仍含 "pending"/"resolved"），符合真实 idle 行为（决定 1）。
- 新增目标 1 / 目标 2 用例（见 §6）。

### 5.3 文档

- 本文件（最终版）。

## 6. 测试方案

复用 `FakeSessionManager`（可编排 `(method, path)` → 响应序列/ history）。所有等待相关用例通过 `todo_stabilize_wait_s=0` 与 `monkeypatch time.sleep` 避免真实等待。

### 6.1 目标 1：idle 后重取

1. **POST 即终态**：POST 返回 `A`，status 立即 idle，history 最新 = `A` → 返回 `A`（等价）。
2. **POST 后追加**：POST 返回 `A`，status idle，history 最新 = `B`（更完整）→ 返回 `B`。
3. **重取脏数据回退**：idle 后重取为空 / prompt 回显 / 等于 previous → 回退返回 `A`。
4. **重取为 compaction**：idle 后最新为 compaction 摘要 → 回退返回 `A`。
5. **更新 `test_pending_word_in_normal_text`**：history 最新与 POST 一致，断言返回 POST 文本，且不误判 TODO。

### 6.2 目标 2：TODO 非空追问

1. **等待后自愈**：首检 TODO_PENDING → 10s 窗后 idle（TODO 清空）→ 不发 nudge，重取返回最新文本。
2. **10s 后回到 running 继续等**：首检 TODO_PENDING → recheck 为 running → 不发 nudge → 回主循环 → 最终 idle 返回（覆盖用户场景）。
3. **追问后收敛**：首检 + recheck 均 TODO_PENDING → 发 1 次 nudge → 之后 idle → 断言发出过 nudge POST，返回 nudge 后最新文本。
4. **nudge 上限**：始终 TODO_PENDING，`max_todo_nudges=1` → 发 1 次后仍未收敛 → 抛 `TimeoutError` → `send_command` 返回 `{"ok": false, ...}`。
5. **nudge 禁用**：`todo_nudge_enabled=False`，TODO 非空 → 不发 nudge，超时收敛抛 `TimeoutError`。
6. **nudge 不递归**：断言 nudge 走 `_post_message_only`，不二次进入 `_await_and_finalize`（统计 POST 次数与等待调用次数）。
7. **nudge 后 fallback**：nudge 后 idle 但重取无效 → 回退到 nudge POST 文本，而非最初 `A`；nudge POST 文本亦无效 → 报错（决定 3）。
8. **previous 刷新**：多轮 nudge 场景下，断言每次 nudge 前 previous 基线已更新（决定 4）。

### 6.3 回归

- 现有 `test_session_manager_guard.py` 全部用例通过（空响应恢复、compaction 拒绝、stale history 拒绝、prompt echo 拒绝、`wait_for_idle` True/False 等）。
- `PYTHONPATH=src python3 -m pytest src/tests/test_session_manager_guard.py -q`
- 全量：`PYTHONPATH=src python3 -m pytest src/tests -q`

### 6.4 真实环境冒烟（可选）

- 对 4097 server 用 `scripts/diagnose_seam_opencode.py --mode message` 验证 round-trip 不回归。
- 一个短 E2E（`--max-iter 1`）确认 phase 输出仍可被 validator 解析。

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| nudge 与会话互踩长循环 | `_max_todo_nudges` 上限 + 统一 `deadline` 双约束 |
| nudge 污染最终输出 | 文案强约束“仅返回原 prompt 要求 + 清理超范围 TODO” |
| 重取拿到中间态/工具噪声 | idle 判定要求非 running，重取后仍做防污染校验 |
| TODO 误报（`None` 当 pending） | 仅 `_session_has_incomplete_todos() is True` 触发 |
| 旧 `wait_for_idle` 调用者语义变化 | `return_on_todo_pending=False` + 保持时间节奏（决定 2） |
| nudge 后返回旧中间态 | fallback 切到 nudge POST 文本，无效则报错（决定 3） |
| 小 timeout 被 nudge 拖超时 | 不特判，按原 `TimeoutError` 路线处理（决定 6） |

## 8. 实施步骤

1. 加常量与 `__init__` 配置（含 env 覆盖）。
2. 引入 `IdleOutcome` 与 `_await_idle_state()`；`wait_for_idle()` 改薄委托；跑 `test_wait_for_idle_*` 回归。
3. 抽 `_is_usable_refetched_text()`，让 `_recover_empty_response_text()` 复用。
4. 实现 `_refetch_final_text()`。
5. 抽 `_classify_post_failure()`；实现 `_post_message_only()`、`_send_todo_nudge()`、`_build_todo_nudge_prompt()`。
6. 实现 `_await_and_finalize()`；改写 `_send_message_raw()` 非空分支。
7. 更新 `test_pending_word_in_normal_text`；补 §6 新用例。
8. 跑 `test_session_manager_guard.py` 与全量测试。
9. 真实冒烟（可选），收尾。

#!/usr/bin/env python3
"""OpenCode Config Checker

验证 .opencode/opencode.jsonc 配置是否有效，并测试 LLM 连接。

用法:
    python scripts/check_opencode_config.py [--config /path/to/opencode.jsonc]
"""
from __future__ import annotations

import importlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def strip_comments(text: str) -> str:
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


def load_config(config_path: Path) -> dict[str, Any]:
    raw = config_path.read_text(encoding="utf-8")
    lines = raw.split('\n')
    cleaned_lines = []
    for line in lines:
        in_string = False
        escape_next = False
        comment_start = -1
        for i, ch in enumerate(line):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
            if not in_string and ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
                comment_start = i
                break
        if comment_start >= 0:
            cleaned_lines.append(line[:comment_start])
        else:
            cleaned_lines.append(line)
    cleaned = '\n'.join(cleaned_lines)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 JSONC: {exc}") from exc


def check_server_alive(base_url: str) -> bool:
    """Check if OpenCode server is running."""
    try:
        url = base_url.rstrip("/") + "/global/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.getcode() == 200:
                print(f"  ✅ Server alive at {base_url}")
                return True
    except Exception as exc:
        print(f"  ❌ Server not reachable at {base_url}: {exc}")
    return False


def check_session_api(base_url: str) -> bool:
    """Check if session API is accessible."""
    try:
        url = base_url.rstrip("/") + "/session"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"  ✅ Session API OK, sessions: {len(data) if isinstance(data, list) else '?'}")
            return True
    except Exception as exc:
        print(f"  ❌ Session API failed: {exc}")
    return False


def check_agent_api(base_url: str) -> list[str]:
    """Check available agents."""
    try:
        url = base_url.rstrip("/") + "/agent"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            agents = []
            for a in data:
                name = a.get("name", "?")
                mode = a.get("mode", "?")
                agents.append(f"{name} ({mode})")
            print(f"  ✅ {len(agents)} agents available:")
            for a in agents:
                print(f"     - {a}")
            return agents
    except Exception as exc:
        print(f"  ❌ Agent API failed: {exc}")
        return []


def test_llm_call(base_url: str, config: dict[str, Any]) -> bool:
    """Send a test message to verify LLM is actually reachable."""
    try:
        # Create a test session
        url = base_url.rstrip("/") + "/session"
        payload = json.dumps({
            "role": "config_check",
            "agent": "build",
            "lifecycle": "ephemeral",
            "title": "ConfigCheck"
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            sid = data.get("id") or data.get("data", {}).get("id", "")

        if not sid:
            print(f"  ❌ Failed to create test session: {data}")
            return False

        print(f"  ✅ Test session created: {sid}")

        # Send a simple prompt
        msg_url = base_url.rstrip("/") + f"/session/{sid}/message"
        msg_payload = json.dumps({
            "content": "Say 'OK' and nothing else.",
            "parts": [{"type": "text", "text": "Say 'OK' and nothing else."}]
        }).encode()
        msg_req = urllib.request.Request(
            msg_url, data=msg_payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(msg_req, timeout=10) as resp:
            msg_resp = json.loads(resp.read())

        # Wait for idle
        for i in range(60):
            time.sleep(1)
            status_url = base_url.rstrip("/") + f"/session/{sid}/status"
            status_req = urllib.request.Request(status_url)
            with urllib.request.urlopen(status_req, timeout=5) as s_resp:
                status_data = json.loads(s_resp.read())
            st = status_data.get("status", {})
            token = st.get("token", "") if isinstance(st, dict) else ""
            if token not in ("RUNNING", "STREAMING"):
                break
            if i % 10 == 0:
                print(f"  ⏳ LLM processing... ({i}s)")

        # Get response
        last_url = base_url.rstrip("/") + f"/session/{sid}/message"
        last_req = urllib.request.Request(last_url + "?limit=1")
        with urllib.request.urlopen(last_req, timeout=5) as last_resp:
            last_data = json.loads(last_resp.read())
        
        response_text = extract_message_text(last_data)

        # Check response
        if "error" in response_text.lower() or "forbidden" in response_text.lower():
            print(f"  ❌ LLM returned error: {response_text[:200]}")
            cleanup_session(base_url, sid)
            return False
        
        if len(response_text) > 0:
            print(f"  ✅ LLM responded successfully: '{response_text[:100]}'")
            cleanup_session(base_url, sid)
            return True
        
        print(f"  ❌ LLM returned empty response")
        cleanup_session(base_url, sid)
        return False

    except Exception as exc:
        print(f"  ❌ LLM test failed: {exc}")
        return False


def extract_message_text(payload: Any) -> str:
    """Extract text from OpenCode message response."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        for item in reversed(payload):
            text = extract_message_text(item)
            if text:
                return text
        return ""
    if isinstance(payload, dict):
        # Check parts
        parts = payload.get("parts")
        if isinstance(parts, list):
            texts = []
            for part in parts:
                t = extract_message_text(part)
                if t:
                    texts.append(t)
            if texts:
                return " ".join(texts)
        
        # Check content/text
        for key in ("content", "text"):
            val = payload.get(key)
            if isinstance(val, str) and val:
                return val
        
        # Check error
        error = payload.get("error")
        if isinstance(error, dict):
            data = error.get("data", {})
            if isinstance(data, dict):
                msg = data.get("message", "")
                if msg:
                    return f"ERROR: {msg}"
            if isinstance(error, str):
                return f"ERROR: {error}"
        
        # Check nested
        for key in ("message", "data", "response"):
            nested = payload.get(key)
            text = extract_message_text(nested)
            if text:
                return text
    return ""


def cleanup_session(base_url: str, sid: str) -> None:
    """Clean up test session."""
    try:
        abort_url = base_url.rstrip("/") + f"/session/{sid}/abort"
        req = urllib.request.Request(abort_url, method="POST")
        urllib.request.urlopen(req, timeout=5)
        
        del_url = base_url.rstrip("/") + f"/session/{sid}"
        req = urllib.request.Request(del_url, method="DELETE")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def main() -> None:
    print("=" * 60)
    print(" OpenCode Config Checker")
    print("=" * 60)

    config_path = PROJECT_ROOT / ".opencode" / "opencode.jsonc"
    
    print(f"\n1️⃣  检查配置文件: {config_path}")
    if not config_path.exists():
        print(f"  ❌ 配置文件不存在")
        sys.exit(1)
    print(f"  ✅ 配置文件存在")

    config = load_config(config_path)
    
    print(f"\n2️⃣  检查 Provider 配置:")
    providers = config.get("provider", {})
    
    for provider_name, provider_config in providers.items():
        print(f"\n  Provider: {provider_name}")
        options = provider_config.get("options", {})
        
        if "apiKey" in options:
            api_key = options["apiKey"]
            masked = api_key[:8] + "..." if len(api_key) > 16 else "***"
            print(f"    ✅ API Key: {masked}")
        else:
            print(f"    ⚠️  未设置 API Key (可能使用环境变量)")
        
        if "baseURL" in options:
            print(f"    ✅ Base URL: {options['baseURL']}")
        
        models = provider_config.get("models", {})
        if models:
            print(f"    📦 {len(models)} 个模型:")
            for model_name, model_config in models.items():
                print(f"       - {model_name}")
    
    print(f"\n3️⃣  检查 Oh-My-OpenCode 配置 (.opencode/oh-my-opencode.json):")
    omo_config_path = PROJECT_ROOT / ".opencode" / "oh-my-opencode.json"
    if omo_config_path.exists():
        omo_config = json.loads(omo_config_path.read_text())
        
        agents = omo_config.get("agents", {})
        print(f"  ✅ {len(agents)} 个自定义 Agent:")
        for name, cfg in agents.items():
            print(f"     - {name}: {cfg.get('model', '?')}")
        
        categories = omo_config.get("categories", {})
        print(f"  ✅ {len(categories)} 个 Category:")
        for name, cfg in categories.items():
            print(f"     - {name}: {cfg.get('model', '?')}")
    else:
        print(f"  ⚠️  oh-my-opencode.json 不存在")

    # 测试服务器连接
    hostname = "127.0.0.1"
    port = 4098
    server_type = "opencode"
    base_url = f"http://{hostname}:{port}"
    
    print(f"\n4️⃣  测试 OpenCode Server ({base_url}):")
    if not check_server_alive(base_url):
        print(f"  请确保 OpenCode Server 已启动: opencode serve --port 4098 --hostname 127.0.0.1")
        sys.exit(1)
    
    check_session_api(base_url)
    agents = check_agent_api(base_url)

    # 测试 LLM
    print(f"\n5️⃣  测试 LLM 调用 (可能需 10-30s):")
    llm_ok = test_llm_call(base_url, config)

    print(f"\n{'=' * 60}")
    if llm_ok:
        print(" ✅ 所有检查通过！可以运行 E2E 测试")
        print(f"\n运行命令:")
        print("  python -m tests.e2e.e2e_test_v2 \\")
        print("    --project-dir /path/to/cuda_project \\")
        print(f"    --hostname {hostname} \")
        print(f"    --port {port} \")
        print(f"    --server_type {server_type} \")
        print("    --output_dir output_projects \")
        print("    --keep-temp-dir --review-gate")
    else:
        print(" ❌ LLM 调用失败，请检查配置")
        print(f"\n可能的问题:")
        print(f"  1. API Key 无效或已过期")
        print(f"  2. Base URL 配置错误")
        print(f"  3. 模型名称不匹配")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

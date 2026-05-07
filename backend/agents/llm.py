from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


def runtime_status() -> dict[str, str]:
    if os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_DEPLOYMENT"):
        return {"provider": "Azure OpenAI", "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "azure-openai-deployment"), "status": "configured"}
    if os.getenv("OPENAI_API_KEY"):
        return {"provider": "OpenAI", "model": os.getenv("OPENAI_MODEL", "gpt-4.1"), "status": "configured"}
    if os.getenv("GROQ_API_KEY"):
        return {"provider": "Groq", "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"), "status": "configured"}
    return {"provider": "Local", "model": "deterministic-tools", "status": "Set Azure OpenAI, OpenAI, or Groq env vars for LLM rewriting"}


def configured() -> bool:
    return runtime_status()["provider"] != "Local"


def rewrite_code(path: str, content: str, from_version: str, to_version: str, inventory: dict[str, Any]) -> str | None:
    if not configured():
        return None
    payload = {
        "task": "Migrate this .NET source file. Return only strict JSON with key migrated_content.",
        "file": path,
        "from_version": from_version,
        "to_version": to_version,
        "project_inventory": {
            "frameworks": inventory.get("frameworks"),
            "packages": inventory.get("packages"),
            "patterns": inventory.get("patterns"),
        },
        "content": content[:18000],
    }
    parsed = call_model(payload)
    if isinstance(parsed, dict):
        migrated = parsed.get("migrated_content") or parsed.get("content")
        return str(migrated) if migrated else None
    return None


def explain_build_errors(build_output: str, inventory: dict[str, Any]) -> list[str]:
    if not configured() or not build_output:
        return []
    payload = {
        "task": "Summarize .NET build errors into actionable migration fixes. Return strict JSON with key fixes as list of strings.",
        "inventory": inventory,
        "build_output": build_output[-16000:],
    }
    parsed = call_model(payload)
    fixes = parsed.get("fixes", []) if isinstance(parsed, dict) else []
    return [str(item) for item in fixes][:12]


def call_model(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = runtime_status()
    try:
        if status["provider"] == "Azure OpenAI":
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
            deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
            url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
            headers = {"Content-Type": "application/json", "api-key": os.getenv("AZURE_OPENAI_API_KEY", "")}
            body = {"messages": messages(payload), "temperature": 0.1, "max_tokens": 4000}
        elif status["provider"] == "OpenAI":
            url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"}
            body = {"model": status["model"], "messages": messages(payload), "temperature": 0.1, "max_tokens": 4000}
        elif status["provider"] == "Groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"}
            body = {"model": status["model"], "messages": messages(payload), "temperature": 0.1, "max_tokens": 4000}
        else:
            return None
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        response = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
        content = response["choices"][0]["message"]["content"].strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
        if fenced:
            content = fenced.group(1).strip()
        return json.loads(content)
    except Exception:
        return None


def messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are a senior .NET migration agent. Preserve business behavior. Return strict JSON only."},
        {"role": "user", "content": json.dumps(payload, indent=2)},
    ]

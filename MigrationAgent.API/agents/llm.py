from groq import Groq
import os
import time
import re

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Token tracking ────────────────────────────────────────────────────────
_stats = {
    "total_tokens": 0,
    "total_llm_calls": 0,
    "total_executions": 0,
    "by_agent": {},
}

CONTEXT_WINDOW = 128000  # llama-3.3-70b-versatile context window

def reset_token_stats():
    _stats["total_tokens"] = 0
    _stats["total_llm_calls"] = 0
    _stats["total_executions"] = 0
    _stats["by_agent"] = {}

def increment_execution():
    _stats["total_executions"] += 1

def get_token_stats() -> dict:
    calls = _stats["total_llm_calls"]
    execs = _stats["total_executions"]
    total = _stats["total_tokens"]
    avg_per_call = round(total / calls, 1) if calls else 0
    return {
        "total_tokens":             total,
        "total_executions":         execs,
        "total_llm_calls":          calls,
        "avg_tokens_per_execution": round(total / execs, 1) if execs else 0,
        "avg_tokens_per_llm_call":  avg_per_call,
        "context_window":           CONTEXT_WINDOW,
        "context_window_pct":       round((avg_per_call / CONTEXT_WINDOW) * 100, 1) if avg_per_call else 0,
        "model":                    MODEL,
        "by_agent":                 dict(_stats["by_agent"]),
    }

def _load_api_keys() -> list[str]:
    keys = []
    # Support GROQ_API_KEY_1, GROQ_API_KEY_2, ... for multiple keys
    for i in range(1, 10):
        key = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if key:
            keys.append(key)
    # Also support single GROQ_API_KEY
    single = os.environ.get("GROQ_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


def _chat(messages: list, agent_name: str = "Unknown") -> str:
    api_keys = _load_api_keys()
    if not api_keys:
        raise Exception(
            "No Groq API keys configured. "
            "Set GROQ_API_KEY or GROQ_API_KEY_1 / GROQ_API_KEY_2 in your .env file."
        )
    last_error = None
    for attempt in range(3):
        for key in api_keys:
            try:
                client = Groq(api_key=key)
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=4096,
                )
                usage = getattr(response, "usage", None)
                if usage:
                    tokens = getattr(usage, "total_tokens", 0)
                    _stats["total_tokens"] += tokens
                    # Track per-agent
                    _stats["by_agent"][agent_name] = _stats["by_agent"].get(agent_name, 0) + tokens
                _stats["total_llm_calls"] += 1
                return response.choices[0].message.content
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "rate_limit" in error_str:
                    wait_match = re.search(r'try again in ([\d\.]+)s', error_str)
                    wait_time = float(wait_match.group(1)) + 2 if wait_match else 25
                    time.sleep(wait_time)
                continue
        if attempt < 2:
            time.sleep(30)
    raise Exception(f"All Groq API keys failed after retries. Last error: {last_error}")


def ask(prompt: str, agent_name: str = "Unknown") -> str:
    return _chat([{"role": "user", "content": prompt}], agent_name)


def ask_with_system(system: str, prompt: str, agent_name: str = "Unknown") -> str:
    return _chat([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt}
    ], agent_name)


def check_connection() -> bool:
    return len(_load_api_keys()) > 0

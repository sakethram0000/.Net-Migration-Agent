from groq import Groq
import os
import time
import re

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

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


def _chat(messages: list) -> str:
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


def ask(prompt: str) -> str:
    return _chat([{"role": "user", "content": prompt}])


def ask_with_system(system: str, prompt: str) -> str:
    return _chat([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt}
    ])


def check_connection() -> bool:
    return len(_load_api_keys()) > 0

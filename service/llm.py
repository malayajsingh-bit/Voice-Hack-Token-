"""One thin door to the LiteLLM gateway. Every model call in CHEETAAAH goes
through here so cost, retries and logging live in one place."""
import json
import time

import requests

import config

requests.packages.urllib3.disable_warnings()

_BASE, _KEY = None, None


def _creds():
    global _BASE, _KEY
    if _BASE is None:
        _BASE, _KEY = config.creds()
    return _BASE, _KEY


def chat(messages, model, *, json_mode=False, max_tokens=8000, temperature=0, label="",
         reasoning="none"):
    """Returns (text, cost_usd). Retries 429s with backoff; raises on other errors."""
    base, key = _creds()
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": messages}
    if reasoning and ("anthropic" in model or "claude" in model):
        # Claude via the gateway otherwise spends the whole budget thinking on long
        # transcripts and returns no text. Gemini endpoints reject the parameter.
        body["reasoning"] = {"effort": reasoning}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(6):
        r = requests.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json=body, verify=False, timeout=900)
        if r.status_code != 429:
            break
        wait = 20 * (attempt + 1)
        print(f"  {label}rate limited, waiting {wait}s ...", flush=True)
        time.sleep(wait)
    if r.status_code != 200:
        raise RuntimeError(f"gateway {r.status_code}: {r.text[:400]}")
    j = r.json()
    choice = (j.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    cost = float((j.get("usage") or {}).get("cost") or 0)
    if not text and choice.get("finish_reason") == "length" and max_tokens < 40000:
        # the model spent the budget on reasoning; give it room and try once more
        print(f"  {label}hit max_tokens={max_tokens} with no text, retrying with {max_tokens*2}",
              flush=True)
        t2, c2 = chat(messages, model, json_mode=json_mode, max_tokens=max_tokens * 2,
                      temperature=temperature, label=label, reasoning=reasoning)
        return t2, cost + c2
    if not text:
        raise RuntimeError(f"empty completion (finish_reason={choice.get('finish_reason')})")
    return text, cost


def chat_json(messages, model, **kw):
    """chat() that parses the reply as JSON, tolerating a ```json fence."""
    text, cost = chat(messages, model, json_mode=True, **kw)
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(t), cost

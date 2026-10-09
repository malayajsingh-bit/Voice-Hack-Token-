#!/usr/bin/env python3
"""Create or update the Sarvam agent from agent.yaml + prompt.md + the service's tool
definitions.   python3 agent/deploy.py [--dry]

The exact request shape for agents is the one thing to confirm with the Sarvam desk;
this script keeps it in one place (build_payload) so nothing else changes."""
import argparse
import json
import pathlib
import sys

import requests
import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "service"))
import config   # noqa: E402
import sarvam   # noqa: E402

SPEAKERS = {("female", "delhi"): "anushka", ("female", "neutral"): "manisha", ("male", "delhi"): "abhilash",
            ("male", "neutral"): "karun"}   # Bulbul speaker ids; adjust to the current catalogue


def build_payload():
    a = yaml.safe_load((HERE / "agent.yaml").read_text(encoding="utf-8"))
    prompt = (HERE / a["prompt_file"]).read_text(encoding="utf-8")
    return {
        "name": a["name"], "language": a["language"],
        "stt": a["stt"], "tts": {"model": a["voice"]["model"], "speaker": SPEAKERS[("female", "neutral")]},
        "llm": a["llm"], "prompt": prompt, "variables": a["variables"],
        "states": a["states"], "tools": sarvam.tool_definitions(config.PUBLIC_URL),
        "guardrails": a["guardrails"],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    p = build_payload()
    if args.dry or not config.SARVAM_API_KEY:
        print(json.dumps(p, indent=1, ensure_ascii=False)[:4000])
        print("\n(dry run — set SARVAM_API_KEY in .env to deploy)")
        sys.exit(0)
    url = f"{config.SARVAM_BASE}/v1/conversations/agents"
    if config.SARVAM_AGENT_ID:
        r = requests.patch(f"{url}/{config.SARVAM_AGENT_ID}", headers=sarvam.H(), json=p, timeout=60)
    else:
        r = requests.post(url, headers=sarvam.H(), json=p, timeout=60)
    print(r.status_code, r.text[:800])

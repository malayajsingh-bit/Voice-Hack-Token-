"""Sarvam Conversations client. The endpoint paths below are the ones to confirm with
the Sarvam desk on Day 1 (variables field on outbound calls, post-call transcript
endpoint). Everything else in the service is independent of them."""
import requests

import config

H = lambda: {"api-subscription-key": config.SARVAM_API_KEY or "", "Content-Type": "application/json"}


def start_call(phone, variables, agent_id=None):
    """Outbound call with per-call variables. Returns Sarvam's call id."""
    body = {"agent_id": agent_id or config.SARVAM_AGENT_ID, "to": phone, "variables": variables}
    r = requests.post(f"{config.SARVAM_BASE}/v1/conversations/calls", headers=H(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def get_call(call_id):
    """Transcript + recording for a finished call (pull; a webhook is the push alternative)."""
    r = requests.get(f"{config.SARVAM_BASE}/v1/conversations/calls/{call_id}", headers=H(), timeout=30)
    r.raise_for_status()
    return r.json()


def update_agent(agent_id, prompt=None, tools=None, states=None):
    """Push an approved prompt (or tools/states) to the live agent. Only called from
    the dashboard's promote action, never from the audit pipeline directly."""
    body = {k: v for k, v in {"prompt": prompt, "tools": tools, "states": states}.items() if v is not None}
    r = requests.patch(f"{config.SARVAM_BASE}/v1/conversations/agents/{agent_id}", headers=H(), json=body,
                       timeout=30)
    r.raise_for_status()
    return r.json()


def tool_definitions(public_url):
    """The API tools to register on the agent, pointing at this service."""
    T = lambda name, desc, params, states=None: {
        "name": name, "description": desc, "method": "POST", "url": f"{public_url}/tools/{name}",
        "parameters": params, **({"states": states} if states else {})}
    return [
        T("get_seller_context", "Seller profile, categories, demand and last call. Use if variables are empty or you need details.",
          {"glid": "string"}),
        T("set_persona", "Call when the seller sounds rushed, frustrated, confused, interested, switches language, or goes silent. Follow the returned instruction.",
          {"glid": "string", "call_id": "string", "signal": "rushed|frustrated|confused|interested|language_hindi|language_english|silence", "note": "string"}),
        T("flag_sales_ready", "Call when the seller is engaged: asks about plans, prices, results, or agrees to a meeting. Moves the call to pre-sales.",
          {"glid": "string", "call_id": "string", "reason": "string", "confidence": "number"}, ["discover", "objection"]),
        T("get_demand_pitch", "Numbers to quote in pre-sales: buyers per month, business value, AOV, BuyLeads in their city, what they could make.",
          {"glid": "string"}, ["presales"]),
        T("book_callback", "Book a callback or meeting time the seller agreed to.",
          {"glid": "string", "call_id": "string", "when": "string", "note": "string"}, ["presales", "objection"]),
        T("flag_risk", "Call when: wrong requirement, seller frustrated, conversation looping, you are unsure what they said, or they ask for a human.",
          {"glid": "string", "call_id": "string", "kind": "wrong_requirement|frustration|loop|low_confidence|human_request", "note": "string"}),
    ]

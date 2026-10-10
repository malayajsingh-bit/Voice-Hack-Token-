"""Sarvam Conversations REST client (from docs.sarvam.ai/api-reference, 9 Oct 2026).

Base: https://apps.sarvam.ai/api  ·  header: X-API-Key
  POST /outbounds/v1/orgs/{org}/workspaces/{ws}/outbounds        start one outbound call
       body: app_config{app_id, app_version, connection_config{connection_id, agent_phone_number},
             agent_variables{...}, app_overrides{initial_state_name, initial_language_name}},
             user_config{user_phone_number}, webhook_config{url, metadata}
       -> {attempt_id}
  GET  /analytics/v1/{org}/{ws}/{app}/interactions?start_datetime&end_datetime&limit&offset
  GET  /analytics/v1/{org}/{ws}/{app}/transcripts/{interaction_id}
  GET  /analytics/v1/{org}/{ws}/{app}/recordings/{interaction_id}

Agent creation/update and HTTP tools go through the MCP server (configure_agent,
create_http_tool) — there is no public REST path documented for them."""
import time

import requests

import config

BASE = "https://apps.sarvam.ai/api"


def H():
    return {"X-API-Key": config.SARVAM_API_KEY or "", "api-subscription-key": config.SARVAM_API_KEY or "",
            "Content-Type": "application/json"}


def ids():
    need = {"SARVAM_ORG_ID": config._env("SARVAM_ORG_ID"), "SARVAM_WORKSPACE_ID": config._env("SARVAM_WORKSPACE_ID"),
            "SARVAM_APP_ID": config._env("SARVAM_APP_ID") or config.SARVAM_AGENT_ID,
            "SARVAM_APP_VERSION": config._env("SARVAM_APP_VERSION", "1"),
            "SARVAM_CONNECTION_ID": config._env("SARVAM_CONNECTION_ID"),
            "SARVAM_AGENT_PHONE": config._env("SARVAM_AGENT_PHONE")}
    missing = [k for k, v in need.items() if not v]
    if missing:
        raise RuntimeError("missing in .env: " + ", ".join(missing))
    return need


def start_call(phone, variables, webhook_url=None, initial_state=None, language="Hindi"):
    i = ids()
    tk = config._env("TOOL_KEY")
    wh = webhook_url or f"{config.PUBLIC_URL}/calls/ingest"
    if tk and "k=" not in wh:
        wh = wh + ("&" if "?" in wh else "?") + f"k={tk}"
    body = {"app_config": {"app_id": i["SARVAM_APP_ID"], "app_version": int(i["SARVAM_APP_VERSION"]),
                           "connection_config": {"connection_id": i["SARVAM_CONNECTION_ID"],
                                                 "agent_phone_number": i["SARVAM_AGENT_PHONE"]},
                           "agent_variables": {k: str(v) for k, v in variables.items() if v is not None},
                           "app_type": "agent",
                           "app_overrides": {k: v for k, v in {"initial_state_name": initial_state,
                                                                "initial_language_name": language}.items() if v}},
            "user_config": {"user_phone_number": phone},
            "webhook_config": {"url": wh,
                               "metadata": {"glid": variables.get("glid"), "call_id": variables.get("call_id")}}}
    r = requests.post(f"{BASE}/outbounds/v1/orgs/{i['SARVAM_ORG_ID']}/workspaces/{i['SARVAM_WORKSPACE_ID']}/outbounds",
                      headers=H(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()          # {"attempt_id": ...}


def _an(path, **params):
    i = ids()
    r = requests.get(f"{BASE}/analytics/v1/{i['SARVAM_ORG_ID']}/{i['SARVAM_WORKSPACE_ID']}/{i['SARVAM_APP_ID']}/{path}",
                     headers=H(), params=params or None, timeout=60)
    r.raise_for_status()
    return r.json()


def interactions(hours=24, limit=100, offset=0):
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
    return _an("interactions", start_datetime=start, end_datetime=end, limit=limit, offset=offset,
               sort_by="start_datetime", sort_order="desc")


def transcript(interaction_id):
    return _an(f"transcripts/{interaction_id}")


def recording(interaction_id):
    return _an(f"recordings/{interaction_id}")


def tool_definitions(public_url):
    """The HTTP tools to register on the agent (via MCP create_http_tool), pointing here."""
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

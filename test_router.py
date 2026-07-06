import urllib.request, json, uuid
from datetime import datetime, timezone

imf = {
    "request_id": str(uuid.uuid4()),
    "trace_id": str(uuid.uuid4()),
    "span_id": "",
    "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
    "user": {"user_id":"poc-user","department":"poc","roles":["developer"],"auth_method":"api_key"},
    "request": {"messages":[{"role":"user","content":"test"}],"model":"llama3.2:3b","stream":False,"max_tokens":2048,"temperature":0.7},
    "governance": {"pii_masked":False,"pii_fields_detected":[],"injection_score":0.0,"jailbreak_score":0.0,"content_safety_passed":True,"human_approval_required":False},
    "routing": {"selected_model":None,"routing_mode":"auto","fallback_level":0},
    "cache": {"lookup_hit":False,"cache_key":None},
    "response": {"content":None,"finish_reason":None,"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}},
    "metadata": {},
    "extensions": {}
}
data = json.dumps(imf).encode()
req = urllib.request.Request("http://llm-poc-router:8082/route", data=data, headers={"Content-Type":"application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=5)
    print("Status:", r.status)
    print(r.read()[:500].decode())
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code)
    print(e.read()[:500].decode())
except Exception as e:
    print("Error:", type(e).__name__, str(e)[:200])
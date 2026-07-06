import urllib.request, json, uuid
from datetime import datetime, timezone

imf = {
    "request_id": str(uuid.uuid4()),
    "trace_id": str(uuid.uuid4()),
    "span_id": "",
    "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
    "user": {"user_id":"poc-user","department":"poc","roles":["developer"],"auth_method":"api_key"},
    "request": {"messages":[{"role":"user","content":"Say hello briefly"}],"model":"llama3.2:3b","stream":False,"max_tokens":50,"temperature":0.7},
    "governance": {"pii_masked":False,"pii_fields_detected":[],"injection_score":0.0,"jailbreak_score":0.0,"content_safety_passed":True,"human_approval_required":False},
    "routing": {"selected_model":"llama3.2:3b","routing_mode":"auto","fallback_level":0},
    "cache": {"lookup_hit":False,"cache_key":None},
    "response": {"content":None,"finish_reason":None,"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}},
    "metadata": {},
    "extensions": {}
}
data = json.dumps(imf).encode()
req = urllib.request.Request("http://inference-adapter:8087/infer", data=data, headers={"Content-Type":"application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=120)
    print("Status:", r.status)
    body = json.loads(r.read().decode())
    print("Response content:", body.get("response",{}).get("content",""))
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code, e.read()[:300].decode())
except Exception as e:
    print("Error:", type(e).__name__, str(e)[:200])
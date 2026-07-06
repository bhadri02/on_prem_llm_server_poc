import urllib.request, json
data = json.dumps({"request_id": "test"}).encode()
req = urllib.request.Request(
    "http://llm-poc-router:8082/route",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    r = urllib.request.urlopen(req, timeout=5)
    print("Status:", r.status)
except Exception as e:
    print("Error:", type(e).__name__, str(e)[:200])
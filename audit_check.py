import urllib.request, json

r = urllib.request.urlopen('http://localhost:9200/audit/events?limit=100', timeout=5)
events = json.loads(r.read().decode())

print('Total audit events:', len(events))
print()

# PII records
print('--- PII Masking Events ---')
for ev in events:
    pii = ev.get('pii_actions')
    if pii and pii != '[]' and pii != []:
        layer = ev.get('layer', '')
        etype = ev.get('event_type', '')
        outcome = ev.get('outcome', '')
        print(f'  layer={layer}  event={etype}  pii={pii}  outcome={outcome}')

print()
# Router events
router_evs = [e for e in events if e.get('layer') == 'router']
print(f'--- Router Audit Events: {len(router_evs)} ---')
for e in router_evs[:5]:
    print(f'  {e.get("event_type")}  outcome={e.get("outcome")}  latency={e.get("latency_ms")}ms')

print()
# Summary by layer
from collections import Counter
by_layer = Counter(e.get('layer') for e in events)
print('--- Events by Layer ---')
for layer, count in sorted(by_layer.items()):
    print(f'  {layer}: {count}')

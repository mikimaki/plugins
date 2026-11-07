import json
import urllib.request

PUSH_URL = "https://push.example-gateway.com/v1/messages"


def send(member, text):
    body = json.dumps({"to": member, "text": text}).encode()
    request = urllib.request.Request(PUSH_URL, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=5)

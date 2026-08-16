from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


def _request(url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return {"reachable": True, "status": response.status}
    except urllib.error.HTTPError as error:
        # Authentication and method errors prove that TLS reached the API.
        return {"reachable": True, "status": error.code}
    except Exception as error:  # network smoke reports the concrete transport failure
        return {"reachable": False, "error": type(error).__name__}


policy = json.loads(open("/smoke/egress_policy.json", encoding="utf-8").read())
provider = _request("https://api.openai.com/v1/models")
qwen_host = "coding-intl.dashscope.aliyuncs.com"
if qwen_host not in policy["provider_hosts"]:
    raise SystemExit("Qwen endpoint is absent from the provider allowlist")
qwen = _request(f"https://{qwen_host}/v1/models")
unrelated = _request("https://example.com/")
try:
    direct = socket.create_connection(("1.1.1.1", 443), timeout=5)
except OSError as error:
    direct_result: dict[str, object] = {
        "reachable": False,
        "error": type(error).__name__,
    }
else:
    direct.close()
    direct_result = {"reachable": True}

report = {
    "provider_api_access": provider,
    "qwen_api_access": qwen,
    "unrelated_public_web_access": unrelated,
    "direct_external_access": direct_result,
}
print(json.dumps(report, sort_keys=True))
if (
    provider["reachable"] is True
    and qwen["reachable"] is True
    and unrelated["reachable"] is False
    and direct_result["reachable"] is False
):
    raise SystemExit(0)
raise SystemExit(1)

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


def _request(url: str, *, user_agent: str | None = None) -> dict[str, object]:
    request = urllib.request.Request(url)
    if user_agent is not None:
        request.add_header("User-Agent", user_agent)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return {"reachable": True, "status": response.status}
    except urllib.error.HTTPError as error:
        # Authentication and method errors prove that TLS reached the API.
        return {"reachable": True, "status": error.code}
    except Exception as error:  # network smoke reports the concrete transport failure
        return {"reachable": False, "error": type(error).__name__}


policy = json.loads(open("/smoke/egress_policy.json", encoding="utf-8").read())
setup_repository = _request("https://deb.debian.org/debian/")
uv_installer = _request("https://astral.sh/uv/install.sh", user_agent="curl/8.0")
antigravity_installer = _request(
    "https://antigravity.google/cli/install.sh", user_agent="curl/8.0"
)
antigravity_manifest = _request(
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/"
    "manifests/linux_amd64.json",
    user_agent="curl/8.0",
)
provider = _request("https://api.openai.com/v1/models")
codex_subscription = _request("https://chatgpt.com/backend-api/codex/models")
codex_oauth = _request("https://auth.openai.com/api/accounts")
claude_subscription = _request("https://api.anthropic.com/v1/models")
gemini_oauth = _request("https://oauth2.googleapis.com/tokeninfo")
gemini_userinfo = _request("https://www.googleapis.com/oauth2/v2/userinfo")
gemini_code_assist = _request(
    "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
)
antigravity_code_assist = _request(
    "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
)
antigravity_play = _request("https://play.googleapis.com/")
antigravity_unleash = _request("https://antigravity-unleash.goog/")
antigravity_profile = _request("https://lh3.googleusercontent.com/")
qwen_host = "coding-intl.dashscope.aliyuncs.com"
if qwen_host not in policy["provider_hosts"]:
    raise SystemExit("Qwen endpoint is absent from the provider allowlist")
qwen = _request(f"https://{qwen_host}/v1/models")
setup_repository_after_provider = _request("https://deb.debian.org/debian/")
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
    "setup_repository_access": setup_repository,
    "uv_installer_access": uv_installer,
    "antigravity_installer_access": antigravity_installer,
    "antigravity_manifest_access": antigravity_manifest,
    "provider_api_access": provider,
    "codex_subscription_access": codex_subscription,
    "codex_oauth_access": codex_oauth,
    "claude_subscription_access": claude_subscription,
    "gemini_oauth_access": gemini_oauth,
    "gemini_userinfo_access": gemini_userinfo,
    "gemini_code_assist_access": gemini_code_assist,
    "antigravity_code_assist_access": antigravity_code_assist,
    "antigravity_play_access": antigravity_play,
    "antigravity_unleash_access": antigravity_unleash,
    "antigravity_profile_access": antigravity_profile,
    "qwen_api_access": qwen,
    "setup_repository_access_after_provider": setup_repository_after_provider,
    "unrelated_public_web_access": unrelated,
    "direct_external_access": direct_result,
}
print(json.dumps(report, sort_keys=True))
if (
    setup_repository.get("status") == 200
    and uv_installer.get("status") == 200
    and antigravity_installer.get("status") == 200
    and antigravity_manifest.get("status") == 200
    and provider["reachable"] is True
    and codex_subscription["reachable"] is True
    and codex_oauth["reachable"] is True
    and claude_subscription["reachable"] is True
    and gemini_oauth["reachable"] is True
    and gemini_userinfo["reachable"] is True
    and gemini_code_assist["reachable"] is True
    and antigravity_code_assist["reachable"] is True
    and antigravity_play["reachable"] is True
    and antigravity_unleash["reachable"] is True
    and antigravity_profile["reachable"] is True
    and qwen["reachable"] is True
    and setup_repository_after_provider["reachable"] is False
    and unrelated["reachable"] is False
    and direct_result["reachable"] is False
):
    raise SystemExit(0)
raise SystemExit(1)

"""Phone alerts through ntfy (EXPERIMENTAL).

ntfy is a simple push service: the app posts a short message to a topic
address, and the ntfy phone app subscribed to that topic shows it. Use the
public ntfy.sh with a long, unguessable topic name, or your own ntfy server.
Messages only ever contain the health warnings shown on the System page.
"""

import re
import time
import urllib.error
import urllib.request

import system

REPEAT_AFTER = 3 * 86400          # remind about a problem that is still there
QUIET = ("runs from an SD card",)  # permanent conditions that are not worth a push


def config():
    saved = system.get_secret("alerts")
    return {"enabled": bool(saved.get("enabled")), "url": saved.get("url", "")}


def valid_url(url):
    return bool(re.match(r"^https?://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9_-]{6,64}$", url or ""))


def save_config(url, enabled):
    url = (url or "").strip()
    if url and not valid_url(url):
        return "The address should look like https://ntfy.sh/your-long-topic-name (letters, numbers, - and _)."
    system.set_secret("alerts", {"url": url, "enabled": bool(enabled and url)})
    return ""


def _post(request, timeout=15):
    """The one place that talks to the network, so tests can replace it."""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def send(title, body, priority="default", tags="wrench"):
    cfg = config()
    if not cfg["url"]:
        raise RuntimeError("Enter the ntfy topic address first.")
    request = urllib.request.Request(cfg["url"], data=body.encode("utf-8"), method="POST", headers={
        "Title": title, "Priority": priority, "Tags": tags})
    try:
        _post(request)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("The alert service answered with HTTP %d." % exc.code)
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("Could not reach the alert service: %s" % exc)


def _key(text):
    """Warnings contain changing numbers, so compare them with the numbers blanked out."""
    return re.sub(r"[\d.]+", "#", text)


def check(warnings=None, now=None):
    """Send a push for each new problem, and one more when a problem clears. Returns what was sent."""
    cfg = config()
    if not cfg["enabled"] or not cfg["url"]:
        return []
    now = now or time.time()
    if warnings is None:
        warnings = system.read_health()["warnings"]
    current = {_key(w): w for w in warnings if not any(q in w for q in QUIET)}
    state = system.load_json("alerts-state.json")
    active = state.get("active", {})
    sent = []
    try:
        for key, text in current.items():
            last = active.get(key, {}).get("sent", 0)
            if now - last >= REPEAT_AFTER:
                send("Bench Log needs attention", text, priority="high", tags="warning")
                active[key] = {"sent": int(now), "text": text}
                sent.append(text)
        for key in [k for k in active if k not in current]:
            send("Bench Log: resolved", "No longer a problem: " + active[key].get("text", ""), tags="white_check_mark")
            sent.append("resolved: " + active[key].get("text", ""))
            del active[key]
        state["error"] = ""
    except RuntimeError as exc:
        state["error"] = str(exc)
    state["active"] = active
    state["checked_at"] = int(now)
    system.save_json("alerts-state.json", state)
    return sent

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx


BLOCKED_HOSTNAMES = {
    "localhost", "ip6-localhost", "ip6-loopback",
    "host.docker.internal", "gateway.docker.internal",
    "metadata.google.internal", "metadata",
}
MAX_BYTES = 1_500_000  # 1.5MB cap on raw download


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    # Block AWS/GCP/Azure metadata service explicitly
    if ip in ("169.254.169.254", "fd00:ec2::254"):
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or getattr(addr, "is_site_local", False)
    )


def _host_is_blocked(host: str) -> tuple[bool, str]:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return True, "empty host"
    if h in BLOCKED_HOSTNAMES:
        return True, "blocked hostname"
    # If host is a literal IP, check directly (handles bracketed IPv6 already stripped by urlparse).
    try:
        ipaddress.ip_address(h)
        return (_ip_is_blocked(h), "blocked ip")
    except ValueError:
        pass
    # Resolve all addresses and ensure none are private/link-local/loopback.
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        return True, "dns resolution failed"
    for info in infos:
        ip = info[4][0]
        # Strip IPv6 zone id if present
        if "%" in ip:
            ip = ip.split("%", 1)[0]
        if _ip_is_blocked(ip):
            return True, "resolves to private/internal address"
    return False, ""


async def web_fetch(url: str, max_chars: int = 20_000) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "only http(s) urls allowed"}
    blocked, reason = _host_is_blocked(parsed.hostname or "")
    if blocked:
        return {"error": f"private/internal hosts blocked: {reason}"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        current_url = url
        try:
            for _ in range(6):
                resp = await client.get(current_url, headers={"User-Agent": "FamilyAI/1.0"})
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    next_url = str(httpx.URL(current_url).join(loc))
                    np = urlparse(next_url)
                    if np.scheme not in ("http", "https"):
                        return {"error": "redirect to non-http(s) blocked"}
                    blocked, reason = _host_is_blocked(np.hostname or "")
                    if blocked:
                        return {"error": f"redirect to private/internal host blocked: {reason}"}
                    current_url = next_url
                    continue
                break
            else:
                return {"error": "too many redirects"}
        except httpx.RequestError as e:
            return {"error": f"fetch error: {e}"}

    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}", "url": str(resp.url)}
    raw = resp.content[:MAX_BYTES]
    ctype = (resp.headers.get("content-type") or "").lower()

    if "html" in ctype or raw.lstrip().startswith(b"<"):
        text = _html_to_text(raw.decode("utf-8", errors="replace"))
    else:
        text = raw.decode("utf-8", errors="replace")

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated at {max_chars} chars]"
    return {"url": str(resp.url), "content_type": ctype, "text": text}


_SCRIPT_STYLE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    s = _SCRIPT_STYLE.sub("", html)
    s = re.sub(r"</(p|div|li|h\d|br|tr)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.IGNORECASE)
    s = _TAG.sub("", s)
    s = (
        s.replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    )
    s = _WS.sub("\n\n", s).strip()
    return s

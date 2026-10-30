import time
import urllib.request
from pathlib import Path
import re

env_file = Path(__file__).resolve().parent.parent / ".env"

def get_target_url():
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8")
        m = re.search(r"MINI_APP_URL=(\S+)", text)
        if m:
            base = m.group(1).rstrip("/")
            return f"{base}/api/health"
    return None

consecutive_failures = 0

while True:
    url = get_target_url()
    if url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TunnelKeepalive/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    print(f"[Keepalive] Non-200 status: {resp.status} for {url}", flush=True)
        except Exception as e:
            consecutive_failures += 1
            print(f"[Keepalive] Ping failed ({consecutive_failures}) for {url}: {e}", flush=True)
    else:
        print("[Keepalive] No MINI_APP_URL found in .env", flush=True)
    
    time.sleep(20)

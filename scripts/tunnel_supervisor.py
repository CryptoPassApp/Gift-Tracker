import os
import sys
import time
import subprocess

env = os.environ.copy()
env["PATH"] = "D:\\;" + env.get("PATH", "")

while True:
    try:
        print("[Supervisor] Starting Tunnelmole...", flush=True)
        proc = subprocess.Popen(
            ["cmd.exe", "/c", "D:\\npx.cmd -y tunnelmole 8020"],
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        proc.wait()
        print(f"[Supervisor] Tunnelmole exited with code {proc.returncode}. Restarting in 2s...", flush=True)
    except Exception as exc:
        print(f"[Supervisor] Error running tunnelmole: {exc}", flush=True)
    time.sleep(2)

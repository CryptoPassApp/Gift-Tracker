with open("start_local.ps1", "rb") as f:
    data = f.read().decode("utf-8", errors="replace")

import re
data = re.sub(r"type = 'web_app'; text = '[^']*'", "type = 'web_app'; text = 'Open App'", data)

with open("start_local.ps1", "wb") as f:
    f.write(data.encode("utf-8"))

print("start_local.ps1 successfully fixed")

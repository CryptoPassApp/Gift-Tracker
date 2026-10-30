with open("start_local.ps1", "r", encoding="utf-8") as f:
    text = f.read()

target = "text = 'Sniper'"
replacement = "text = 'Открыть'"
assert target in text, "target not found"
text = text.replace(target, replacement, 1)

with open("start_local.ps1", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated start_local.ps1 menu button text")

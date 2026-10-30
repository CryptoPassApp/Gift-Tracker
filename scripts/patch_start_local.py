with open("start_local.ps1", "r", encoding="utf-8") as f:
    text = f.read()

target = """        # Tunnelmole — основной вариант: HTTPS без браузерной заглушки.
        $npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
        if ($npx) {
            Write-Host 'TUNNEL: trying tunnelmole...'"""

replacement = """        # Tunnelmole — основной вариант: HTTPS без браузерной заглушки.
        if ($env:Path -notlike '*D:\\*') { $env:Path = 'D:\\;' + $env:Path }
        $npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
        if (-not $npx -and (Test-Path 'D:\\npx.cmd')) { $npx = 'D:\\npx.cmd' }
        if ($npx) {
            Write-Host 'TUNNEL: trying tunnelmole...'"""

assert target in text, "target string not found"
text = text.replace(target, replacement)

with open("start_local.ps1", "w", encoding="utf-8") as f:
    f.write(text)

print("start_local.ps1 patched successfully!")

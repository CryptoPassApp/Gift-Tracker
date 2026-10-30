$ErrorActionPreference = 'SilentlyContinue'
$log = "C:\Users\Нико\.zcode\workspace\f7dd6fd0-2717-4a02-88bb-95556b60b317\gift_tracker\logs\tunnel_daemon.log"
while ($true) {
    cmd.exe /c "set PATH=D:\;%PATH% && D:\npx.cmd -y tunnelmole 8020" | Out-File -FilePath $log -Encoding utf8
    Start-Sleep -Seconds 2
}

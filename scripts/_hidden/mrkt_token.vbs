' MRKT Token Service в скрытом окне (запуск из start_local.ps1).
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c python -u scripts\mrkt_token_service.py >> logs\mrkt_token.out.log 2>&1", 0, False

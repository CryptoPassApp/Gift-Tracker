Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c python -u -m uvicorn api.main:app --host 127.0.0.1 --port 8020 > logs\api.out.log 2> logs\api.err.log", 0, False

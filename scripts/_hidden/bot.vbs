Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c python -u -m bot.main > logs\bot.out.log 2> logs\bot.err.log", 0, False

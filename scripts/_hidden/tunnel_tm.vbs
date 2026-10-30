Set sh = CreateObject("WScript.Shell")
sh.Environment("PROCESS")("PATH") = "D:;" & sh.Environment("PROCESS")("PATH")
sh.Run "cmd.exe /c ""D:\npx.cmd"" -y tunnelmole 8020 > logs\tunnel.out.log 2> logs\tunnel.err.log", 0, False

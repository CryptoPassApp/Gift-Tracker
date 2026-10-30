Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:8020 serveo.net > logs\tunnel.out.log 2> logs\tunnel.err.log", 0, False

' ngrok in a hidden window, direct launch (no cmd: Cyrillic path broke it).
Set sh = CreateObject("WScript.Shell")
la = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
cmd = """" & la & "\Microsoft\WindowsApps\ngrok.exe"" start gifttracker" & _
      " --config """ & la & "\ngrok\ngrok.yml"" --config ""ngrok_policy.yml""" & _
      " --log ""logs\ngrok.log"" --log-format json"
sh.Run cmd, 0, False

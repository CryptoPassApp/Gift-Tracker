from pathlib import Path

p = Path(r"C:\Users\Нико\.zcode\workspace\f7dd6fd0-2717-4a02-88bb-95556b60b317\gift_tracker\start_local.ps1")
content = p.read_text(encoding="utf-8")

old_text = "'-R', '80:127.0.0.1:8020', 'serveo.net' `"
new_text = "'-R', '80:127.0.0.1:8020', 'serveo.net' `\n                -WindowStyle Hidden -RedirectStandardOutput $tOut -RedirectStandardError $tErr"

if old_text in content:
    content = content.replace(old_text, new_text)
    p.write_text(content, encoding="utf-8")
    print("SUCCESS")
else:
    print("NOT FOUND")

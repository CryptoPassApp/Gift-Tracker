with open("static/index.html", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add <meta name="referrer" content="no-referrer">
target_head = '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">'
replacement_head = '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">\n    <meta name="referrer" content="no-referrer">'

assert target_head in text, "target_head not found"
text = text.replace(target_head, replacement_head, 1)

# 2. Add URL bar sanitization for tgWebAppData
target_tg = "        if (tg) {\n            try {\n                tg.ready();"
replacement_tg = """        // Очистка URL хэша/query от чувствительного tgWebAppData для защиты от утечки
        if (window.history && window.history.replaceState && window.location.hash.includes('tgWebAppData')) {
            try { window.history.replaceState(null, '', window.location.pathname + window.location.search); } catch (e) {}
        }

        if (tg) {
            try {
                tg.ready();"""

assert target_tg in text, "target_tg not found"
text = text.replace(target_tg, replacement_tg, 1)

with open("static/index.html", "w", encoding="utf-8") as f:
    f.write(text)

print("static/index.html successfully hardened!")

with open("static/index.html", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update the stats card HTML
target1 = '<p class="num text-xs font-extrabold text-[#00F0FF]">$5.20</p>'
replacement1 = '<p id="tonUsdRate" class="num text-xs font-extrabold text-[#00F0FF]">$1.24</p>'
assert target1 in text, "target1 not found"
text = text.replace(target1, replacement1, 1)

# 2. Update TON_USD_RATE variable
target2 = 'const TON_USD_RATE = 5.20;          // 1 TON = $5.20'
replacement2 = 'let TON_USD_RATE = 1.24;            // 1 TON = $1.24 (live dynamic)'
assert target2 in text, "target2 not found"
text = text.replace(target2, replacement2, 1)

# 3. Add updateTonUsdRate function
target3 = 'async function boot() {'
replacement3 = '''async function updateTonUsdRate() {
            try {
                const ep = (isTelegramEnv && tg && tg.initData) ? '/api/gifts/rates' : '/api/demo/rates';
                const res = await api(ep);
                if (res && res.ton_usd && res.ton_usd > 0) {
                    TON_USD_RATE = Number(res.ton_usd);
                    const el = document.getElementById('tonUsdRate');
                    if (el) el.textContent = '$' + TON_USD_RATE.toFixed(2);
                }
            } catch (e) {
                // fallback remains
            }
        }

        async function boot() {
            updateTonUsdRate();'''

assert target3 in text, "target3 not found"
text = text.replace(target3, replacement3, 1)

with open("static/index.html", "w", encoding="utf-8") as f:
    f.write(text)

print("static/index.html successfully updated")

with open("static/index.html", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Favicon in head
target_head = '<link rel="preconnect" href="https://fonts.googleapis.com">'
replacement_head = '<link rel="icon" type="image/png" href="logo.png">\n    <link rel="apple-touch-icon" href="logo.png">\n    <link rel="preconnect" href="https://fonts.googleapis.com">'
assert target_head in text, "target_head not found"
text = text.replace(target_head, replacement_head, 1)

# 2. Header logo
target_header = """<header class="flex items-center justify-between mb-4">
            <div class="flex items-center gap-2.5">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-[#00E676] to-[#00b894] flex items-center justify-center shadow-[0_0_22px_rgba(0,230,118,0.35)]">
                    <svg class="w-5 h-5 text-[#0B0E17]" fill="none" aria-hidden="true"><use href="#i-bolt"></use></svg>
                </div>
                <div>
                    <h1 class="text-[17px] font-extrabold tracking-tight leading-none">GIFTTRACKER</h1>
                    <p class="text-[10px] text-[#475569] font-bold uppercase tracking-widest mt-1">Sniper Terminal</p>
                </div>
            </div>"""

replacement_header = """<header class="flex items-center justify-between mb-4">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl overflow-hidden border border-[#00F0FF]/30 shadow-[0_0_20px_rgba(0,240,255,0.25)] flex items-center justify-center bg-[#0B0E17] flex-shrink-0">
                    <img src="logo.png" alt="Logo" class="w-full h-full object-cover select-none" draggable="false">
                </div>
                <div>
                    <h1 class="text-[17px] font-extrabold tracking-tight leading-none text-white">GIFTTRACKER</h1>
                    <p class="text-[10px] text-[#00F0FF] font-bold uppercase tracking-widest mt-1 flex items-center gap-1">
                        <span class="inline-block w-1.5 h-1.5 rounded-full bg-[#00E676] animate-pulse"></span>
                        Arbitrage Terminal
                    </p>
                </div>
            </div>"""

assert target_header in text, "target_header not found"
text = text.replace(target_header, replacement_header, 1)

with open("static/index.html", "w", encoding="utf-8") as f:
    f.write(text)

print("static/index.html header updated with custom logo")

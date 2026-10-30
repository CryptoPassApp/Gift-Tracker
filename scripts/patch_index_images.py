with open("static/index.html", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update imgTag
old_imgTag = """        function imgTag(url, alt, cls, extra) {
            return '<img src="' + esc(url) + '" alt="' + esc(alt || '') + '" loading="lazy" draggable="false" ' +
                'onerror="this.onerror=null;this.src=\\'' + FALLBACK_IMAGE + '\\'" ' +
                'class="' + (cls || 'w-14 h-14 object-contain') + '" ' + (extra || '') + '>';
        }"""

new_imgTag = """        function imgTag(url, alt, cls, extra, fallback) {
            const fb = fallback || FALLBACK_IMAGE;
            return '<img src="' + esc(url) + '" alt="' + esc(alt || '') + '" loading="lazy" draggable="false" ' +
                'onerror="this.onerror=null;this.src=\\'' + esc(fb) + '\\'" ' +
                'class="' + (cls || 'w-14 h-14 object-contain') + '" ' + (extra || '') + '>';
        }"""

# 2. Update dealCardHTML
old_dealCard = """        function dealCardHTML(sig, animate) {
            const gift = GIFT_BY_NAME[sig.gift_name] || { name: sig.gift_name, image: FALLBACK_IMAGE };
            const isHot = sig.discount_pct >= HOT_THRESHOLD;
            const image = sig.image_url || gift.image || FALLBACK_IMAGE;"""

new_dealCard = """        function dealCardHTML(sig, animate) {
            const gift = GIFT_BY_NAME[sig.gift_name] || { name: sig.gift_name, image: FALLBACK_IMAGE };
            const isHot = sig.discount_pct >= HOT_THRESHOLD;
            let image = sig.image_url;
            if ((!image || image.includes('/collection/')) && sig.lot_number && gift.fragmentSlug) {
                const num = String(sig.lot_number).replace(/[^0-9]/g, '');
                if (num) {
                    image = fragmentModelImage(gift, num);
                }
            }
            if (!image) image = gift.image || FALLBACK_IMAGE;"""

# 3. Update imgTag inside dealCardHTML
old_dealImg = """imgTag(image, gift.name, 'deal-img relative w-16 h-16 rounded-2xl object-cover ring-1 ring-white/15 shadow-[0_8px_20px_rgba(0,0,0,0.5)]')"""
new_dealImg = """imgTag(image, gift.name, 'deal-img relative w-16 h-16 rounded-2xl object-cover ring-1 ring-white/15 shadow-[0_8px_20px_rgba(0,0,0,0.5)]', '', gift.image || FALLBACK_IMAGE)"""

assert old_imgTag in text, "old_imgTag not matched"
text = text.replace(old_imgTag, new_imgTag)

assert old_dealCard in text, "old_dealCard not matched"
text = text.replace(old_dealCard, new_dealCard)

assert old_dealImg in text, "old_dealImg not matched"
text = text.replace(old_dealImg, new_dealImg)

with open("static/index.html", "w", encoding="utf-8") as f:
    f.write(text)

print("SUCCESS: static/index.html updated successfully!")

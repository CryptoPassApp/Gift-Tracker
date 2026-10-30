import sqlite3
conn = sqlite3.connect("data/gift_tracker.db")
c = conn.cursor()
c.execute("SELECT id, gift_name, lot_number, image_url FROM snipe_signals ORDER BY id DESC LIMIT 15")
for r in c.fetchall():
    print(r)








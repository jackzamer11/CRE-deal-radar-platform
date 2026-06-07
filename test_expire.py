import sqlite3
conn = sqlite3.connect('backend/deal_radar.db')
conn.execute("UPDATE companies SET lease_expiry_date = '2026-08-21' WHERE name = 'Dean & Company'")
conn.commit()
print('Done')
conn.close()
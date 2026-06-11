import requests
with open(r"C:\Users\Jackz\Downloads\report (6).pdf", "rb") as f:
    r = requests.post("http://localhost:8000/api/lease-comps/debug-text", files={"file": f})
    data = r.json()
    for p in data["pages"][:2]:
        print("--- PAGE", p["page"], "---")
        for line in p["lines"]:
            print(repr(line))

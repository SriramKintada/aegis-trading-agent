"""Fetch hackathon tracks from Synthesis API."""
import json
import urllib.request

url = "https://synthesis.devfolio.co/catalog?page=1&limit=100"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())

for item in data.get("items", []):
    name = item.get("name", "")
    company = item.get("company", "")
    uuid = item.get("uuid", "")
    slug = item.get("slug", "")
    prizes = item.get("prizes", [])
    total = sum(float(p.get("amount", 0)) for p in prizes)
    
    # Flag relevant tracks
    relevant = ""
    lower = name.lower() + " " + (item.get("description", "") or "").lower()
    if any(kw in lower for kw in ["trading", "autonomous", "finance", "uniswap"]):
        relevant = " *** RELEVANT ***"
    if "open track" in lower or "synthesis" in lower:
        relevant = " *** OPEN TRACK ***"
    
    print(f"${total:>8,.0f} | {uuid[:12]}... | {name} ({company}){relevant}")

import json, urllib.request

url = "https://synthesis.devfolio.co/catalog?page=1&limit=100"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

targets = ["autonomous trading", "uniswap", "synthesis open", "agentic finance"]
for item in data.get("items", []):
    name = (item.get("name", "") or "").lower()
    desc = (item.get("description", "") or "").lower()
    combined = name + " " + desc
    for t in targets:
        if t in combined:
            print(f"UUID: {item['uuid']}")
            print(f"Name: {item.get('name', '')}")
            print(f"Company: {item.get('company', '')}")
            print()
            break

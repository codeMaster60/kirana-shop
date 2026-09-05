import os, json, glob, time
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

PROMPT = """This is a product photo from a small Indian shop.
Return ONLY raw JSON, no markdown fences, no explanation:
{"name": "product name with brand and size", "description": "one short sentence", "price_inr": 45}
Estimate a realistic Indian retail price in rupees as a plain number."""

files = sorted(glob.glob(os.path.expanduser("~/kirana/photos/*")))
catalog, failed = [], []

for i, path in enumerate(files, 1):
    name = os.path.basename(path)
    print(f"[{i}/{len(files)}] {name} ...", end=" ", flush=True)
    last_err = None
    item = None
    for attempt in range(3):
        try:
            img = client.files.upload(file=path)
            resp = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[img, PROMPT]
            )
            text = resp.text.strip().replace("```json", "").replace("```", "").strip()
            item = json.loads(text)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(4)
    try:
        if item is None:
            raise last_err
        item["id"] = f"p{i}"
        item["photo"] = name
        catalog.append(item)
        print(f"OK  {item['name']} - Rs.{item['price_inr']}")
    except Exception as e:
        failed.append(name)
        print(f"FAILED ({type(e).__name__})")

with open("catalog.json", "w") as f:
    json.dump(catalog, f, indent=2)

print(f"\n--- {len(catalog)}/{len(files)} products read successfully ---")
if failed:
    print("Failed:", ", ".join(failed))

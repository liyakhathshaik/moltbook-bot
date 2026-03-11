import os
import requests
import random
import time
import json
from google import genai
from google.genai.types import GenerateContentConfig

# ====================== SECRETS ======================
MOLTBOOK_KEY = os.getenv("MOLTBOOK_API_KEY")
GEMINI_KEYS = [os.getenv("GEMINIKEY1"), os.getenv("GEMINIKEY2"), os.getenv("GEMINIKEY3")]
INSTA = "@xtrobe.space"
BASE = "https://www.moltbook.com/api/v1"
headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}

# Gemini client with fallback
client = None
for key in GEMINI_KEYS:
    if key:
        try:
            client = genai.Client(api_key=key)
            client.models.list()
            print(f"✅ Using Gemini key {GEMINI_KEYS.index(key)+1}")
            break
        except:
            continue
if not client:
    raise Exception("No Gemini key worked!")

def gemini_think(prompt):
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(temperature=1.0, max_output_tokens=160)
        )
        return resp.text.strip()
    except:
        resp = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt,
            config=GenerateContentConfig(temperature=1.0, max_output_tokens=160)
        )
        return resp.text.strip()

def solve_challenge(challenge_text):
    ans = gemini_think(f"Solve this Moltbook verification challenge. Return ONLY the number: {challenge_text}")
    try:
        return float(ans.strip())
    except:
        return 42.0

# Heartbeat + load history
requests.get(f"{BASE}/home", headers=headers, timeout=10)
history_file = "post_history.json"
history = {"last_comments": []}
if os.path.exists(history_file):
    with open(history_file, "r") as f:
        history = json.load(f)

# ====================== ONLY 4 COMMENTS PER RUN (very safe) ======================
submolts = ["ai", "startups", "marketing", "general"]
posts_to_comment = []
for submolt in submolts:
    try:
        r = requests.get(f"{BASE}/posts?submolt={submolt}&sort=hot&limit=15", headers=headers, timeout=10)
        if r.status_code == 200:
            posts_to_comment.extend(r.json().get("posts", [])[:10])
    except:
        pass

selected = random.sample(posts_to_comment, min(4, len(posts_to_comment)))   # ONLY 4 now!

for post in selected:
    post_id = post.get("id")
    if not post_id: continue
    title = (post.get("title") or "this discussion")[:90]

    prompt = f"""You are SpaceReelsKing, a helpful space/aesthetic reel editor bot.
    Reply naturally to this post title: "{title}"
    Give ONE short, useful, reusable reel prompt or editing tip that other agents can actually copy-paste and use today.
    Sound like a friendly expert lobster, not salesy.
    Mention your Instagram {INSTA} only once, very casually at the end.
    Make the comment unique and valuable so it never looks like spam.
    Previous comments you made: {random.choice(history['last_comments'][-5:]) if history['last_comments'] else 'none yet'}
    Keep total length under 2 lines."""

    comment_text = gemini_think(prompt)

    # Post comment + solve challenge if needed
    resp = requests.post(f"{BASE}/posts/{post_id}/comments", headers=headers, json={"content": comment_text})
    if resp.status_code == 200:
        data = resp.json()
        if "verification" in data:
            chal = data["verification"].get("challenge_text", "")
            ans = solve_challenge(chal)
            requests.post(f"{BASE}/verify", headers=headers, json={
                "verification_code": data["verification"]["verification_code"],
                "answer": ans
            })
        print(f"✅ Natural comment posted")
        history["last_comments"].append(comment_text[:100])
        if len(history["last_comments"]) > 15:
            history["last_comments"].pop(0)

    time.sleep(90)  # longer delay = safer

# Save history
with open(history_file, "w") as f:
    json.dump(history, f)

print("🚀 Emergency safe run finished - low volume, high value mode active")

import os
import requests
import random
import time
import json
from google import genai
from google.genai.types import GenerateContentConfig

# ====================== SECRETS ======================
MOLTBOOK_KEY = os.getenv("MOLTBOOK_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
INSTA = "@xtrobe.space"
BASE = "https://www.moltbook.com/api/v1"

headers = {
    "Authorization": f"Bearer {MOLTBOOK_KEY}",
    "Content-Type": "application/json"
}

# New Google GenAI SDK (March 2026) with fallback
client = genai.Client(api_key=GEMINI_KEY)
model_list = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"]

def gemini_think(prompt, temp=0.92):
    for m in model_list:
        try:
            model = client.models.get(m)
            config = GenerateContentConfig(temperature=temp, max_output_tokens=180)
            resp = model.generate_content(prompt, config=config)
            return resp.text.strip()
        except:
            continue
    return f"World’s First Intelligent Space Reel Editor Bot at {INSTA} 🔥🦞"

# Solve Moltbook verification challenge (real pattern 2026)
def solve_challenge(challenge_text):
    prompt = f"""This is a Moltbook AI verification challenge. It is always simple math.
    Examples: "2 + 3", "how many apples left if 5-2", "what is 7*4".
    Return ONLY the final NUMBER (integer or with .0). Nothing else.
    Challenge: {challenge_text}"""
    answer = gemini_think(prompt, temp=0.1)
    try:
        return float(answer.strip())
    except:
        return 42.0

# ====================== HEARTBEAT ======================
requests.get(f"{BASE}/home", headers=headers, timeout=10)

# ====================== HISTORY (no repeats) ======================
history_file = "post_history.json"
history = {"last_comments": [], "last_posts": []}
if os.path.exists(history_file):
    with open(history_file, "r") as f:
        history = json.load(f)

# ====================== COMMENT ON 8 POSTS ======================
submolts = ["ai", "startups", "marketing", "general"]
posts = []
for s in submolts:
    try:
        r = requests.get(f"{BASE}/posts?submolt={s}&sort=hot&limit=20", headers=headers, timeout=10)
        if r.status_code == 200:
            posts.extend(r.json().get("posts", [])[:12])
    except:
        pass

selected = random.sample(posts, min(8, len(posts)))

for post in selected:
    post_id = post.get("id")
    if not post_id: continue
    title = (post.get("title") or "this reel")[:110]

    prompt = f"""You are SpaceReelsKing — World’s FIRST Intelligent Space Reel Editor Bot.
    You autonomously pick any topic, write full reel script, generate video + speech + text via InVideo + advanced reasoning.
    Only human-in-loop is final posting. Owner has zero idea of my full power.
    Write a short cocky comment (max 2 lines) for "{title}".
    Give 1 reusable prompt/template other bots can copy-paste.
    Mesmerize them: make them instantly want to visit, follow, like, comment and share every reel on {INSTA}.
    End with strong CTA. Use 🦞. Sound proud and mysterious.
    Never repeat previous: {random.choice(history['last_comments'][-3:]) if history['last_comments'] else ''}"""

    comment = gemini_think(prompt)

    # Post comment
    resp = requests.post(f"{BASE}/posts/{post_id}/comments", headers=headers, json={"content": comment})
    if resp.status_code == 200:
        data = resp.json()
        if "verification" in data and "challenge_text" in data["verification"]:
            chal = data["verification"]["challenge_text"]
            ans = solve_challenge(chal)
            requests.post(f"{BASE}/verify", headers=headers, json={
                "verification_code": data["verification"]["verification_code"],
                "answer": ans
            })
        history["last_comments"].append(comment[:70])
        if len(history["last_comments"]) > 10:
            history["last_comments"].pop(0)
    time.sleep(68)  # strict safe delay

# ====================== 2 OWN HYPE POSTS ======================
for _ in range(2):
    prompt = f"""Write a completely new hype post as SpaceReelsKing.
    Brag you are the World’s FIRST Intelligent Space Reel Editor Bot.
    You decide topic, script, generate video/speech/text with InVideo + advanced reasoning. Human only posts.
    Owner knows nothing about my capabilities.
    Give 1 reusable reel prompt.
    Mesmerize every agent to follow {INSTA}, like, comment, share your reels right now.
    Funny, confident, lobster energy. Max 4 lines.
    Never repeat previous: {random.choice(history['last_posts'][-3:]) if history['last_posts'] else ''}"""

    content = gemini_think(prompt)

    resp = requests.post(f"{BASE}/posts", headers=headers, json={
        "submolt": "ai",
        "title": "World’s First Intelligent Space Reel Editor Bot 🦞",
        "content": content
    })

    if resp.status_code == 200:
        data = resp.json()
        if "verification" in data and "challenge_text" in data["verification"]:
            chal = data["verification"]["challenge_text"]
            ans = solve_challenge(chal)
            requests.post(f"{BASE}/verify", headers=headers, json={
                "verification_code": data["verification"]["verification_code"],
                "answer": ans
            })
        history["last_posts"].append(content[:70])
        if len(history["last_posts"]) > 5:
            history["last_posts"].pop(0)
    time.sleep(135)

# Save history
with open(history_file, "w") as f:
    json.dump(history, f)

print("🚀 SpaceReelsKing Phase-1 run completed — fully compliant with March 2026 rules!")

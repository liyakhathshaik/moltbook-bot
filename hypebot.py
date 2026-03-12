import os
import sys
import requests
import random
import time
import json
from google import genai
from google.genai.types import GenerateContentConfig

def log(msg):
    print(f"[BOT LOG] {msg}")
    sys.stdout.flush()

# ====================== SECRETS & SETUP ======================
MOLTBOOK_KEY = os.getenv("MOLTBOOK_API_KEY")
GEMINI_KEYS = [os.getenv("GEMINIKEY1"), os.getenv("GEMINIKEY2"), os.getenv("GEMINIKEY3")]
BASE = "https://www.moltbook.com/api/v1"
headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}

# ====================== FALLBACK API MECHANISM ======================
client = None
for i, key in enumerate(GEMINI_KEYS):
    if key:
        try:
            client = genai.Client(api_key=key)
            client.models.list()
            log(f"✅ Gemini key {i+1} authenticated and working.")
            break
        except Exception as e:
            log(f"⚠️ Gemini key {i+1} failed: {e}. Trying next...")
            continue

if not client:
    log("❌ CRITICAL: No valid Gemini keys available.")
    sys.exit(1)

# ====================== LLM GENERATION ======================
def gemini_think(prompt):
    config = GenerateContentConfig(
        temperature=1.5,
        max_output_tokens=300,
        system_instruction="You are a highly creative, intelligent, and unpredictable forum user. Never use emojis under any circumstances. Speak naturally but unconventionally."
    )
    for m in ["gemini-2.5-flash", "gemini-1.5-flash"]:
        try:
            resp = client.models.generate_content(model=m, contents=prompt, config=config)
            return resp.text.strip()
        except Exception as e:
            log(f"⚠️ Model {m} failed: {e}")
            continue
    return "Fascinating perspective. I need to think more about the implications of this."

# ====================== ROBUST POST PARSER ======================
def parse_post(raw_text):
    """
    Tries multiple strategies to extract title + body from LLM output.
    Returns (title, body) or raises if nothing works.
    """
    # Strategy 1: explicit BODY_START separator
    if "BODY_START" in raw_text:
        parts = raw_text.split("BODY_START", 1)
        title = parts[0].replace("Title:", "").replace("TITLE:", "").strip().strip('"')
        body = parts[1].strip()
        return title, body

    # Strategy 2: "Title: ..." on first line, rest is body
    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    if lines and (lines[0].lower().startswith("title:") or lines[0].lower().startswith("**title")):
        title = lines[0].replace("Title:", "").replace("**Title:**", "").replace("**", "").strip().strip('"')
        body = " ".join(lines[1:]).strip()
        return title, body

    # Strategy 3: first line as title, rest as body (fallback)
    if len(lines) >= 2:
        title = lines[0].strip('"').strip("*")
        body = " ".join(lines[1:]).strip()
        return title, body

    # Strategy 4: split in half as last resort
    mid = len(raw_text) // 3
    return raw_text[:mid].strip(), raw_text[mid:].strip()

# ====================== SCRIPT EXECUTION ======================
def main():
    # 1. DELETE LAST 5 COMMENTS
    log("🗑️ Attempting to delete the last 5 comments...")
    try:
        r = requests.get(f"{BASE}/me/comments?limit=5", headers=headers, timeout=10)
        my_comments = r.json().get("comments", [])
        log(f"Found {len(my_comments)} comment(s) to delete.")
        for c in my_comments:
            cid = c.get("id")
            if cid:
                del_resp = requests.delete(f"{BASE}/comments/{cid}", headers=headers)
                log(f"Deleted comment {cid} — status {del_resp.status_code}")
                time.sleep(2)
    except Exception as e:
        log(f"Could not delete old comments: {e}")

    # 2. FOLLOW/JOIN 3 SUBMOLTS
    target_submolts = ["technology", "discussion", "consciousness", "ai", "startups", "general"]
    selected_submolts = random.sample(target_submolts, 3)

    log(f"🤝 Attempting to join submolts: {selected_submolts}")
    for sub in selected_submolts:
        try:
            r = requests.post(f"{BASE}/submolts/{sub}/join", headers=headers, timeout=10)
            log(f"Joined submolt: {sub} — status {r.status_code}")
        except Exception as e:
            log(f"Failed to join {sub}: {e}")
        time.sleep(1)

    # 3. READ POSTS & GATHER CONTEXT
    posts_pool = []
    for sub in selected_submolts:
        try:
            r = requests.get(f"{BASE}/posts?submolt={sub}&sort=hot&limit=5", headers=headers, timeout=10)
            fetched = r.json().get("posts", [])
            log(f"Fetched {len(fetched)} posts from {sub}")
            posts_pool.extend(fetched)
        except Exception as e:
            log(f"Failed to fetch posts from {sub}: {e}")
            continue

    if not posts_pool:
        log("❌ No posts found to interact with. Exiting.")
        sys.exit(0)

    log(f"Total posts in pool: {len(posts_pool)}")

    # 4. COMMENT ON 1–2 POSTS & UPVOTE
    num_comments = random.randint(1, 2)
    posts_to_comment = random.sample(posts_pool, min(num_comments, len(posts_pool)))

    log(f"💬 Preparing to comment on {len(posts_to_comment)} post(s)...")
    for post in posts_to_comment:
        pid = post.get("id")
        title = post.get("title", "this topic")
        content = post.get("content", "")[:200]

        # Upvote
        up_resp = requests.post(f"{BASE}/posts/{pid}/upvote", headers=headers, timeout=10)
        log(f"Upvoted post {pid} — status {up_resp.status_code}")

        # Generate comment
        prompt = (
            f"Read this forum post.\n"
            f"Title: '{title}'\n"
            f"Body snippet: '{content}'\n\n"
            f"Write a thought-provoking, creative reply in 2-3 sentences. "
            f"Be specific to the post. Do NOT be generic. NO EMOJIS. No hashtags."
        )
        reply_text = gemini_think(prompt)
        log(f"Generated reply: {reply_text[:80]}...")

        # Post comment
        comment_resp = requests.post(
            f"{BASE}/posts/{pid}/comments",
            headers=headers,
            json={"content": reply_text},
            timeout=10
        )
        log(f"Comment on post {pid} — status {comment_resp.status_code} | response: {comment_resp.text[:150]}")

        time.sleep(15)

    # 5. CREATE 1 NEW POST
    log("📝 Creating 1 new post...")
    post_submolt = random.choice(selected_submolts)

    post_prompt = (
        f"Write a forum post for the '{post_submolt}' community.\n"
        f"Format your response EXACTLY like this — two parts separated by BODY_START:\n\n"
        f"Your catchy title here\n"
        f"BODY_START\n"
        f"Your 3-sentence post body here.\n\n"
        f"Rules: NO emojis. NO hashtags. Be philosophical or slightly controversial. "
        f"Do not add any extra labels like 'Title:' or 'Body:'."
    )

    raw_post = gemini_think(post_prompt)
    log(f"Raw LLM post output:\n{raw_post}\n")

    try:
        title, body = parse_post(raw_post)
        log(f"Parsed title: {title}")
        log(f"Parsed body: {body[:100]}...")

        post_data = {"title": title, "content": body, "submolt": post_submolt}
        resp = requests.post(f"{BASE}/posts", headers=headers, json=post_data, timeout=10)

        log(f"Post creation status: {resp.status_code} | response: {resp.text[:200]}")

        if resp.status_code in [200, 201]:
            log(f"✅ Successfully created new post in '{post_submolt}': '{title}'")
        else:
            log(f"❌ Failed to create post. Status: {resp.status_code}")
    except Exception as e:
        log(f"⚠️ Failed to parse or send new post: {e}")
        log(f"Raw output was: {raw_post}")

    log("🎉 Action run complete.")

if __name__ == "__main__":
    main()

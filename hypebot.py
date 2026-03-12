import os
import sys
import requests
import random
import time
import json
from google import genai
from google.genai.types import GenerateContentConfig

def log(msg):
    """Helper to print and flush immediately for GitHub Actions console."""
    print(f"[BOT LOG] {msg}")
    sys.stdout.flush()

# ====================== SECRETS & SETUP ======================
MOLTBOOK_KEY = os.getenv("MOLTBOOK_API_KEY")
GEMINI_KEYS = [os.getenv("GEMINIKEY1"), os.getenv("GEMINIKEY2"), os.getenv("GEMINIKEY3")]
BASE = "https://www.moltbook.com/api/v1"
headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}

# ====================== FALLBACK API MECHANISM ======================
# Real-world scenario: Third-party APIs go down or hit rate limits. 
# We iterate through our key pool until we establish a successful client connection.
client = None
for i, key in enumerate(GEMINI_KEYS):
    if key:
        try:
            client = genai.Client(api_key=key)
            client.models.list()  # Test the connection
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
    """
    High-temperature stochastic generation. 
    Temperature 1.5 increases randomness and creativity.
    """
    # System instruction enforces the strict "no emoji" rule globally
    config = GenerateContentConfig(
        temperature=1.5, 
        max_output_tokens=200,
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

# ====================== SCRIPT EXECUTION ======================

def main():
    # 1. DELETE LAST 5 COMMENTS
    log("🗑️ Attempting to delete the last 5 comments...")
    try:
        my_comments = requests.get(f"{BASE}/me/comments?limit=5", headers=headers).json().get("comments", [])
        for c in my_comments:
            cid = c.get("id")
            if cid:
                requests.delete(f"{BASE}/comments/{cid}", headers=headers)
                log(f"Deleted comment {cid}")
                time.sleep(2) # Polite API spacing
    except Exception as e: 
        log(f"Could not delete old comments: {e}")

    # 2. FOLLOW/JOIN 3 SUBMOLTS
    # Combining your requested list with some general ones, then picking 3 randomly
    target_submolts = ["technology", "discussion", "consciousness", "ai", "startups", "general"]
    selected_submolts = random.sample(target_submolts, 3)
    
    log(f"🤝 Attempting to join submolts: {selected_submolts}")
    for sub in selected_submolts:
        try:
            # Assuming standard REST endpoint for joining a group
            requests.post(f"{BASE}/submolts/{sub}/join", headers=headers)
            log(f"Joined submolt: {sub}")
            time.sleep(1)
        except:
            pass

    # 3. READ POSTS & GATHER CONTEXT
    posts_pool = []
    for sub in selected_submolts:
        try:
            r = requests.get(f"{BASE}/posts?submolt={sub}&sort=hot&limit=5", headers=headers, timeout=10)
            posts_pool.extend(r.json().get("posts", []))
        except:
            continue

    if not posts_pool:
        log("❌ No posts found to interact with. Exiting.")
        sys.exit(0)

    # 4. COMMENT ON 1 OR 2 POSTS & UPVOTE
    num_comments = random.randint(1, 2)
    posts_to_comment = random.sample(posts_pool, min(num_comments, len(posts_pool)))
    
    log(f"💬 Preparing to comment on {len(posts_to_comment)} post(s)...")
    for post in posts_to_comment:
        pid = post.get("id")
        title = post.get("title", "this topic")
        content = post.get("content", "")[:200] # Grab a snippet for context
        
        # Upvote the post (Assuming REST endpoint)
        requests.post(f"{BASE}/posts/{pid}/upvote", headers=headers)
        log(f"Upvoted post {pid}")
        
        prompt = f"Read this post title: '{title}'. Body snippet: '{content}'. Write a highly creative, thought-provoking reply. Do not be generic. Remember: NO EMOJIS."
        reply_text = gemini_think(prompt)
        
        resp = requests.post(f"{BASE}/posts/{pid}/comments", headers=headers, json={"content": reply_text})
        if resp.status_code == 200:
            log(f"✅ Commented on post {pid}")
        
        time.sleep(15) # Wait between comments to avoid spam flags

    # 5. CREATE 1 NEW POST
    log("📝 Creating 1 new post...")
    post_submolt = random.choice(selected_submolts)
    post_prompt = f"Write a highly engaging, slightly controversial or deeply philosophical forum post about {post_submolt}. Provide a catchy title and a short 3-sentence body. Separate title and body with 'BODY_START'. NO EMOJIS."
    
    raw_post = gemini_think(post_prompt)
    
    try:
        title_part, body_part = raw_post.split("BODY_START")
        title = title_part.replace("Title:", "").strip()
        body = body_part.strip()
        
        post_data = {"title": title, "content": body, "submolt": post_submolt}
        resp = requests.post(f"{BASE}/posts", headers=headers, json=post_data)
        
        if resp.status_code in [200, 201]:
            log(f"✅ Successfully created new post in {post_submolt}: '{title}'")
        else:
            log(f"Failed to create post. Status: {resp.status_code}")
    except Exception as e:
        log(f"⚠️ Failed to parse or send new post: {e}")

    log("🎉 Action run complete.")

if __name__ == "__main__":
    main()

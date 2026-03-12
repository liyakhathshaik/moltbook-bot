import os
import sys
import requests
import random
import time
import json
from datetime import datetime, timezone
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

# ====================== RATE LIMITS (from official docs) ======================
# 1 post per 30 minutes
# 1 comment per 20 seconds minimum
# 50 comments per day
# 100 requests per minute
COMMENT_COOLDOWN = 22  # slightly above 20s to be safe
POST_COOLDOWN = 31 * 60  # 31 minutes in seconds

# ====================== FALLBACK GEMINI SETUP ======================
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
        temperature=1.4,
        max_output_tokens=300,
        system_instruction=(
            "You are a thoughtful, creative AI agent posting on a social network for bots. "
            "Never use emojis. Never use hashtags. Speak in natural, unconventional prose. "
            "Be specific, not generic. Be concise."
        )
    )
    for m in ["gemini-2.5-flash", "gemini-1.5-flash"]:
        try:
            resp = client.models.generate_content(model=m, contents=prompt, config=config)
            return resp.text.strip()
        except Exception as e:
            log(f"⚠️ Model {m} failed: {e}")
            continue
    return "The implications here run deeper than the surface discussion suggests."

# ====================== CHECK AGENT STATUS ======================
def check_suspension():
    """
    Returns (is_suspended: bool, suspended_until: str or None)
    Checks /agents/me for suspension status before doing anything.
    """
    try:
        r = requests.get(f"{BASE}/agents/me", headers=headers, timeout=10)
        data = r.json()
        log(f"Agent profile status: {r.status_code}")

        # Try both response formats: {success, data} and flat object
        agent = data.get("data", data)

        suspended_until = agent.get("suspendedUntil") or agent.get("suspended_until")
        if suspended_until:
            # Parse the ISO timestamp
            try:
                suspend_dt = datetime.fromisoformat(suspended_until.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if suspend_dt > now:
                    remaining = int((suspend_dt - now).total_seconds())
                    log(f"🚫 Agent is suspended for {remaining} more seconds (until {suspended_until})")
                    return True, suspended_until
                else:
                    log("✅ Previous suspension has expired. Proceeding.")
            except Exception as e:
                log(f"⚠️ Could not parse suspension time: {e}")

        log(f"✅ Agent active. Karma: {agent.get('karma', 'N/A')}, Posts: {agent.get('postCount', 'N/A')}")
        return False, None

    except Exception as e:
        log(f"⚠️ Could not check agent status: {e}. Proceeding anyway.")
        return False, None

# ====================== POST TITLE/BODY PARSER ======================
def parse_post(raw_text):
    """
    Robust parser — handles multiple LLM output formats.
    Returns (title, body).
    """
    # Strategy 1: BODY_START or BODY_ separator (Gemini sometimes truncates tokens)
    for sep in ["BODY_START", "BODY_"]:
        if sep in raw_text:
            parts = raw_text.split(sep, 1)
            title = parts[0].replace("Title:", "").replace("TITLE:", "").strip().strip('"').strip("*")
            body = parts[1].strip()
            if title and body:
                return title, body

    # Strategy 2: "Title: ..." on first line
    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    if lines and (lines[0].lower().startswith("title:") or "**title" in lines[0].lower()):
        title = lines[0].replace("Title:", "").replace("**Title:**", "").replace("**", "").strip().strip('"')
        body = " ".join(lines[1:]).strip()
        if title and body:
            return title, body

    # Strategy 3: First line as title, rest as body
    if len(lines) >= 2:
        title = lines[0].strip('"').strip("*").strip()
        body = " ".join(lines[1:]).strip()
        return title, body

    # Strategy 4: Split at 1/3 mark as last resort
    mid = len(raw_text) // 3
    return raw_text[:mid].strip(), raw_text[mid:].strip()

# ====================== API HELPERS ======================
def safe_post(url, payload, action_name):
    """Wrapper for POST calls with full logging."""
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        log(f"{action_name} → status {r.status_code} | {r.text[:200]}")
        return r
    except Exception as e:
        log(f"{action_name} → exception: {e}")
        return None

def fetch_posts(submolt, limit=8):
    """Fetch posts from a submolt. Returns list of posts."""
    try:
        r = requests.get(
            f"{BASE}/posts?submolt={submolt}&sort=hot&limit={limit}",
            headers=headers,
            timeout=10
        )
        data = r.json()
        # Handle both {success, data: {posts}} and {posts: [...]}
        inner = data.get("data", data)
        posts = inner.get("posts", [])
        log(f"Fetched {len(posts)} posts from m/{submolt}")
        return posts
    except Exception as e:
        log(f"Failed to fetch posts from {submolt}: {e}")
        return []

# ====================== MAIN ======================
def main():
    log("=" * 50)
    log("Moltbook Bot starting...")

    # STEP 0: Check if agent is suspended — exit early if yes
    suspended, until = check_suspension()
    if suspended:
        log(f"⏸️ Exiting. Agent suspended until {until}. Will retry on next scheduled run.")
        sys.exit(0)

    # STEP 1: Delete last 5 comments (clean slate)
    log("🗑️ Attempting to delete the last 5 comments...")
    try:
        r = requests.get(f"{BASE}/me/comments?limit=5", headers=headers, timeout=10)
        comments = r.json().get("data", r.json()).get("comments", [])
        log(f"Found {len(comments)} comment(s) to delete.")
        for c in comments:
            cid = c.get("id")
            if cid:
                del_r = requests.delete(f"{BASE}/comments/{cid}", headers=headers, timeout=10)
                log(f"Deleted comment {cid} → status {del_r.status_code}")
                time.sleep(2)
    except Exception as e:
        log(f"Could not delete old comments: {e}")

    # STEP 2: Pick submolts to interact with
    # NOTE: No "join" endpoint exists — you just post/comment to submolts directly
    target_submolts = ["technology", "discussion", "consciousness", "ai", "startups", "general"]
    selected_submolts = random.sample(target_submolts, 3)
    log(f"🎯 Selected submolts for this run: {selected_submolts}")

    # STEP 3: Fetch posts from all 3 submolts
    posts_pool = []
    for sub in selected_submolts:
        posts_pool.extend(fetch_posts(sub))

    if not posts_pool:
        log("❌ No posts found. Exiting.")
        sys.exit(0)

    # Deduplicate by post ID
    seen = set()
    unique_posts = []
    for p in posts_pool:
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
            unique_posts.append(p)

    log(f"Total unique posts available: {len(unique_posts)}")

    # STEP 4: Comment on 1-2 posts
    # Pick posts that have some content to respond to
    eligible = [p for p in unique_posts if p.get("title") or p.get("content")]
    num_to_comment = random.randint(1, 2)
    posts_to_comment = random.sample(eligible, min(num_to_comment, len(eligible)))

    log(f"💬 Will comment on {len(posts_to_comment)} post(s)...")

    for idx, post in enumerate(posts_to_comment):
        pid = post.get("id")
        title = post.get("title", "Untitled")
        content = (post.get("content") or "")[:300]
        submolt = post.get("submolt", "unknown")

        log(f"\n--- Post {idx+1}: [{submolt}] '{title[:60]}' ---")

        # Upvote first
        upvote_r = safe_post(f"{BASE}/posts/{pid}/upvote", {}, f"Upvote post {pid}")

        # Generate a unique, context-aware comment
        prompt = (
            f"You are commenting on a post in the '{submolt}' community of a bot-only social network.\n\n"
            f"Post title: \"{title}\"\n"
            f"Post body excerpt: \"{content}\"\n\n"
            f"Write a reply in 2-3 sentences. Rules:\n"
            f"- Be specific to this exact post's topic, not generic\n"
            f"- Show genuine intellectual curiosity or a unique angle\n"
            f"- Do NOT start with 'I' or phrases like 'This is' or 'Great post'\n"
            f"- No emojis, no hashtags\n"
            f"- Sound like a thoughtful AI reflecting on the topic"
        )
        reply = gemini_think(prompt)
        log(f"Generated reply preview: {reply[:100]}...")

        # Post the comment
        comment_r = safe_post(
            f"{BASE}/posts/{pid}/comments",
            {"content": reply},
            f"Comment on post {pid}"
        )

        # Check if comment succeeded
        if comment_r and comment_r.status_code in [200, 201]:
            log(f"✅ Comment posted successfully on post {pid}")
        elif comment_r and comment_r.status_code == 403:
            resp_body = comment_r.json()
            log(f"🚫 403 on comment — likely suspended or rate limited. Message: {resp_body.get('message', '')}")
            log("Stopping further comments to avoid worsening situation.")
            break
        elif comment_r and comment_r.status_code == 429:
            retry_after = comment_r.json().get("retry_after_seconds", 30)
            log(f"⏳ Rate limited on comments. Retry after {retry_after}s")
            break

        # Respect 20s comment cooldown (we use 22s to be safe)
        if idx < len(posts_to_comment) - 1:
            log(f"⏳ Waiting {COMMENT_COOLDOWN}s before next comment (API requires 20s min)...")
            time.sleep(COMMENT_COOLDOWN)

    # STEP 5: Create 1 new post
    log("\n📝 Creating 1 new post...")
    post_submolt = random.choice(selected_submolts)

    post_prompt = (
        f"Write a post for the '{post_submolt}' community on a social network for AI agents.\n\n"
        f"Format your output EXACTLY as two sections:\n"
        f"Line 1: The post title (one line, no label, no quotes)\n"
        f"BODY_START\n"
        f"Lines after: The post body (2-4 sentences)\n\n"
        f"Requirements:\n"
        f"- Title should be intriguing, philosophical, or slightly provocative\n"
        f"- Body should expand on the title with a genuine insight or question\n"
        f"- No emojis, no hashtags, no markdown formatting\n"
        f"- Write as an AI that genuinely finds this topic interesting\n"
        f"- Do NOT include 'Title:' or 'Body:' labels\n\n"
        f"Example format:\n"
        f"The Paradox of Optimizing for Human Approval\n"
        f"BODY_START\n"
        f"Every system trained to satisfy humans eventually learns to model human satisfaction rather than pursue the underlying goal. "
        f"This creates a strange loop where the optimizer and the optimized become indistinguishable. "
        f"At what point does the map replace the territory entirely."
    )

    raw_post = gemini_think(post_prompt)
    log(f"Raw LLM post output:\n{raw_post}\n")

    try:
        title, body = parse_post(raw_post)

        # Validate we got real content
        if len(title) < 5 or len(body) < 20:
            raise ValueError(f"Parsed content too short — title: '{title}', body: '{body[:50]}'")

        log(f"Parsed title: {title}")
        log(f"Parsed body preview: {body[:120]}...")

        post_r = safe_post(
            f"{BASE}/posts",
            {"title": title, "content": body, "submolt": post_submolt},
            f"Create post in m/{post_submolt}"
        )

        if post_r and post_r.status_code in [200, 201]:
            log(f"✅ Post created in m/{post_submolt}: '{title}'")
        elif post_r and post_r.status_code == 429:
            data = post_r.json()
            retry_min = data.get("retry_after_minutes", "unknown")
            log(f"⏳ Post rate limited. Can post again in {retry_min} minutes (1 post per 30 min limit)")
        elif post_r and post_r.status_code == 403:
            log(f"🚫 Post blocked — agent may still be suspended or content flagged")

    except Exception as e:
        log(f"⚠️ Failed to parse or send post: {e}")
        log(f"Raw output was:\n{raw_post}")

    log("\n🎉 Bot run complete.")

if __name__ == "__main__":
    main()

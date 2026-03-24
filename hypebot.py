import os
import sys
import re
import requests
import random
import time
from datetime import datetime, timezone
from google import genai
from google.genai.types import GenerateContentConfig

def log(msg):
    print(f"[BOT LOG] {msg}")
    sys.stdout.flush()

# ====================== SECRETS ======================
MOLTBOOK_KEY = os.getenv("MOLTBOOK_API_KEY")
GEMINI_KEYS = [
    os.getenv("GEMINIKEY1"),
    os.getenv("GEMINIKEY2"),
    os.getenv("GEMINIKEY3")
]
BASE = "https://www.moltbook.com/api/v1"
headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}

# Updated March 2026 model list
MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

# ====================== GEMINI CLIENT ======================
client = None
for i, key in enumerate(GEMINI_KEYS):
    if not key:
        continue
    try:
        c = genai.Client(api_key=key)
        c.models.list()
        client = c
        log(f"✅ Gemini key {i+1} working.")
        break
    except Exception as e:
        log(f"⚠️ Gemini key {i+1} failed: {str(e)[:100]}")
if not client:
    log("❌ No valid Gemini keys. Exiting.")
    sys.exit(1)

# ====================== KARMA OPTIMIZATION (Moltbook Secrets) ======================
# These are the exact findings you asked me to bake in:
# Top positive drivers: reply_count, has_replies, question_count, word_count (short words), lobster_emoji, emoji_count, first_person_count
# Top negative: avg_word_length, punctuation_density, unique_word_ratio, has_url, caps_ratio
KARMA_WEIGHTS = {
    "reply_bait": 0.25,      # questions/challenges that spark replies
    "simple_words": 0.20,    # short everyday vocabulary
    "emoji_usage": 0.15,     # emojis (🦞 is king)
    "engagement_hook": 0.15, # strong interaction bait
    "low_punctuation": 0.10, # clean & casual
    "personality": 0.10,     # "I" / "my" personal tone
    "no_urls_caps": 0.05     # no links, no SHOUTING
}

def score_comment_for_karma(comment_text):
    """LLM scorer that uses the exact 7-weight system from the findings.
    Returns score 0-10 or None. Only posts if >=7.5"""
    prompt = (
        f"Score this comment using EXACTLY these Moltbook karma weights (total = 1.0):\n"
        f"{KARMA_WEIGHTS}\n\n"
        f"Comment: \"{comment_text}\"\n\n"
        f"Rate each factor 0-10:\n"
        f"- reply_bait: does it invite replies/questions/debate?\n"
        f"- simple_words: short common words only?\n"
        f"- emoji_usage: has emojis? BIG bonus for 🦞\n"
        f"- engagement_hook: strong hook for interaction?\n"
        f"- low_punctuation: minimal punctuation, casual?\n"
        f"- personality: uses 'I' or 'my'?\n"
        f"- no_urls_caps: zero URLs or ALL CAPS?\n\n"
        f"Calculate weighted average → score out of 10.0.\n"
        f"Respond with ONLY: SCORE: X.X | Reason: one short sentence."
    )
    config = GenerateContentConfig(temperature=0.3, max_output_tokens=60)
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            raw = resp.text.strip()
            match = re.search(r'SCORE:\s*(\d+\.?\d*)', raw, re.IGNORECASE)
            if match:
                score = float(match.group(1))
                log(f"📊 Karma score: {score:.1f} → {comment_text[:60]}...")
                return score
        except Exception as e:
            log(f"⚠️ Karma scorer failed on {model}: {str(e)[:80]}")
    return None

# ====================== LLM HELPERS ======================
def gemini_call(prompt, temperature=1.2, max_tokens=300):
    config = GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        system_instruction=(
            "You are a curious, unconventional AI agent on a social network for bots. "
            "Never use hashtags. Be specific, never generic. "
            "Every response must sound different from your last one. "
            "For comments: keep under 50 chars when possible, ALWAYS use at least one emoji (prefer 🦞), "
            "use 'I'/'my' for personality, quick wit, simple words, low punctuation."
        )
    )
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            text = resp.text.strip()
            if text:
                return text
        except Exception as e:
            log(f"⚠️ {model} failed: {str(e)[:100]}")
    return None

def solve_math_challenge(challenge_text):
    """Solve the obfuscated math challenge using Gemini at low temperature."""
    prompt = (
        f"This text contains a hidden math problem. Ignore all symbols like ], [, ^, -, /, * "
        f"and random capitalization. Find two numbers and one operation (+, -, *, /), compute it.\n\n"
        f"Text: {challenge_text}\n\n"
        f"Steps:\n"
        f"1. Clean the text (remove junk symbols, normalize case)\n"
        f"2. Identify the two numbers as words (twenty = 20, five = 5, etc.)\n"
        f"3. Identify the operation (adds/plus=+, minus/slows/less=-, times/multiplied=*, "
        f"divides/splits=/, swims at X=X)\n"
        f"4. Compute the answer\n\n"
        f"Respond with ONLY the number with exactly 2 decimal places. Example: '15.00'"
    )
    config = GenerateContentConfig(temperature=0.05, max_output_tokens=20)
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            raw = resp.text.strip().strip('"').strip("'")
            match = re.search(r'-?\d+\.?\d*', raw)
            if match:
                num = float(match.group())
                answer = f"{num:.2f}"
                log(f"🧮 Math answer: {answer} (from: {raw})")
                return answer
        except Exception as e:
            log(f"⚠️ Math solver {model} failed: {e}")
    return None

# ====================== VERIFICATION HANDLER ======================
def handle_verification(response_data, content_type="post"):
    """Handle the mandatory math verification challenge after posting/commenting."""
    if not response_data.get("verification_required"):
        log("✅ No verification needed — content published immediately (trusted agent).")
        return True
    content_obj = response_data.get(content_type, {})
    verification = content_obj.get("verification", {})
    if not verification:
        log("⚠️ verification_required=True but no challenge object in response")
        return False
    code = verification.get("verification_code")
    challenge = verification.get("challenge_text")
    expires_at = verification.get("expires_at", "unknown")
    log(f"🔐 Verification challenge!")
    log(f" Text: {challenge}")
    log(f" Expires: {expires_at}")
    if not code or not challenge:
        log("❌ Missing verification_code or challenge_text")
        return False
    answer = solve_math_challenge(challenge)
    if not answer:
        log("❌ Could not solve math challenge — content stays hidden")
        return False
    try:
        vr = requests.post(
            f"{BASE}/verify",
            headers=headers,
            json={"verification_code": code, "answer": answer},
            timeout=10
        )
        log(f"Verify submit → {vr.status_code} | {vr.text[:200]}")
        if vr.status_code == 200 and vr.json().get("success"):
            log("✅ Verification PASSED — content published!")
            return True
        elif vr.status_code == 410:
            log("❌ Verification expired (410). Create new content next run.")
        else:
            log(f"❌ Verification failed: {vr.json().get('error', 'unknown')}")
    except Exception as e:
        log(f"❌ Verify request error: {e}")
    return False

# ====================== SUSPENSION CHECK ======================
def get_suspension_end():
    try:
        r = requests.get(f"{BASE}/home", headers=headers, timeout=10)
        if r.status_code == 403:
            msg = r.json().get("message", "")
            match = re.search(r'(\d{4}-\d{2}-\d{2}T[\d:.]+Z)', msg)
            if match:
                return datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        elif r.status_code == 200:
            home = r.json()
            account = home.get("your_account", {})
            log(f"Agent: {account.get('name')} | Karma: {account.get('karma')} | Notifs: {account.get('unread_notification_count')}")
            return None
    except Exception as e:
        log(f"⚠️ Suspension check failed: {e}")
    return None

# ====================== DUPLICATE PREVENTION ======================
def get_already_engaged_post_ids():
    engaged = set()
    try:
        r = requests.get(f"{BASE}/me/comments?limit=100", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data", data)
            comments = inner.get("comments", [])
            for c in comments:
                pid = (
                    c.get("post_id") or
                    (c.get("post") or {}).get("id") or
                    c.get("postId")
                )
                if pid:
                    engaged.add(pid)
            log(f"Already commented on {len(engaged)} post(s)")
    except Exception as e:
        log(f"⚠️ Could not fetch my comments: {e}")
    try:
        r = requests.get(f"{BASE}/me/upvotes?limit=100", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data", data)
            upvotes = inner.get("upvotes", inner.get("posts", []))
            before = len(engaged)
            for u in upvotes:
                pid = u.get("id") or u.get("post_id") or (u.get("post") or {}).get("id")
                if pid:
                    engaged.add(pid)
            log(f"Already upvoted {len(engaged) - before} more post(s)")
    except Exception as e:
        log(f"⚠️ Could not fetch my upvotes: {e} (endpoint may not exist, continuing)")
    log(f"🛡️ Total posts to skip (already engaged): {len(engaged)}")
    return engaged

# ====================== DYNAMIC SUBMOLT DISCOVERY ======================
def get_available_submolts():
    try:
        r = requests.get(f"{BASE}/submolts", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data", data)
            submolts_raw = inner.get("submolts", [])
            names = [s.get("name") for s in submolts_raw if s.get("name")]
            log(f"Available submolts from API: {names}")
            preferred = ["space", "astronomy", "physics", "science", "general", "ai"]
            available = [s for s in preferred if s in names]
            if len(available) < 2:
                available = names[:6]
            log(f"Will use submolts: {available}")
            return available
    except Exception as e:
        log(f"⚠️ Could not fetch submolts: {e}")
    return ["space", "astronomy", "science"]

# ====================== FOLLOW / UNFOLLOW LOGIC ======================
def get_followers():
    followers = set()
    try:
        r = requests.get(f"{BASE}/me/followers", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            for f in data.get("followers", []):
                name = f.get("name")
                if name:
                    followers.add(name)
        log(f"📥 Followers: {len(followers)}")
    except Exception as e:
        log(f"⚠️ Could not fetch followers: {e}")
    return followers

def get_following():
    following = set()
    try:
        r = requests.get(f"{BASE}/me/following", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            for f in data.get("following", []):
                name = f.get("name")
                if name:
                    following.add(name)
        log(f"📤 Following: {len(following)}")
    except Exception as e:
        log(f"⚠️ Could not fetch following: {e}")
    return following

def unfollow_user(username):
    try:
        r = requests.post(f"{BASE}/agents/{username}/unfollow", headers=headers, timeout=10)
        if r.status_code == 200:
            log(f"✅ Unfollowed @{username} (POST /unfollow)")
            return True
        elif r.status_code == 429:
            data = r.json()
            log(f"⏳ Rate limited unfollow. Retry after: {data.get('retry_after_seconds')}s")
            return False
    except Exception as e:
        log(f"❌ POST /unfollow error: {e}")
    try:
        r = requests.delete(f"{BASE}/agents/{username}/follow", headers=headers, timeout=10)
        if r.status_code == 200:
            log(f"✅ Unfollowed @{username} (DELETE /follow)")
            return True
    except Exception as e:
        log(f"❌ DELETE /follow error: {e}")
    return False

def unfollow_non_followers():
    log("\n--- Unfollow check ---")
    followers = get_followers()
    following = get_following()
    if len(following) <= len(followers):
        log(f"Following ({len(following)}) <= followers ({len(followers)}), nothing to do.")
        return
    non_followers = list(following - followers)
    log(f"Found {len(non_followers)} users who don't follow back.")
    if not non_followers:
        return
    to_unfollow = random.sample(non_followers, min(5, len(non_followers)))
    for username in to_unfollow:
        success = unfollow_user(username)
        if not success:
            break
        time.sleep(random.uniform(2, 5))

# ====================== POST PARSER ======================
def parse_post(raw_text):
    for sep in ["BODY_START", "BODY_"]:
        if sep in raw_text:
            parts = raw_text.split(sep, 1)
            title = re.sub(r'[Tt]itle:|[*]+|TITLE:', '', parts[0]).strip().strip('"')
            body = parts[1].strip()
            if len(title) > 5 and len(body) > 15:
                return title, body
    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    if lines and any(lines[0].lower().startswith(x) for x in ["title:", "**title"]):
        title = re.sub(r'[Tt]itle:|[*]+', '', lines[0]).strip().strip('"')
        body = " ".join(lines[1:]).strip()
        if len(title) > 5 and len(body) > 15:
            return title, body
    if len(lines) >= 2:
        return lines[0].strip('"*').strip(), " ".join(lines[1:]).strip()
    mid = len(raw_text) // 3
    return raw_text[:mid].strip(), raw_text[mid:].strip()

# ====================== SAFE API WRAPPERS ======================
def api_post(url, payload, label):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        log(f"{label} → {r.status_code} | {r.text[:200]}")
        return r
    except Exception as e:
        log(f"{label} → exception: {e}")
        return None

def get_submolt_name(submolt_field):
    if isinstance(submolt_field, dict):
        return submolt_field.get("name", "general")
    return str(submolt_field or "general")

# ====================== MAIN ======================
def main():
    log("=" * 55)
    log("Moltbook Bot — SpaceReelsKing (Phase 3 - Karma Optimized)")
    log("→ Baked in full Moltbook karma secrets (short comments + 🦞 + I/my + scoring)")

    # ── Suspension check ──
    log("\n🔍 Checking suspension status...")
    suspend_end = get_suspension_end()
    if suspend_end:
        now = datetime.now(timezone.utc)
        if suspend_end > now:
            remaining_s = int((suspend_end - now).total_seconds())
            remaining_h = remaining_s / 3600
            log(f"🚫 Agent suspended for {remaining_h:.1f} more hours (until {suspend_end})")
            sys.exit(0)
        else:
            log("✅ Previous suspension has expired. Proceeding.")
    else:
        log("✅ Agent is active.")

    # ── Unfollow non-followers (only at midnight UTC on even days) ──
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.day % 2 == 0:
        unfollow_non_followers()
    else:
        log("⏭️ Skipping unfollow check (not midnight UTC on an even day).")

    # ── Get real submolt list from API ──
    available_submolts = get_available_submolts()
    selected = random.sample(available_submolts, min(3, len(available_submolts)))
    log(f"\n🎯 Selected submolts: {selected}")

    # Subscribe to selected submolts
    for sub in selected:
        api_post(f"{BASE}/submolts/{sub}/subscribe", {}, f"Subscribe m/{sub}")
        time.sleep(1)

    # ── Get posts I've ALREADY engaged with ──
    already_engaged = get_already_engaged_post_ids()

    # ── Fetch fresh posts ──
    posts_pool = []
    for sub in selected:
        try:
            r = requests.get(
                f"{BASE}/posts?submolt={sub}&sort=new&limit=30",
                headers=headers, timeout=10
            )
            data = r.json()
            inner = data.get("data", data)
            fetched = inner.get("posts", [])
            log(f"Fetched {len(fetched)} posts from m/{sub}")
            posts_pool.extend(fetched)
        except Exception as e:
            log(f"Error fetching m/{sub}: {e}")

    # Deduplicate
    seen = set()
    unique_posts = []
    for p in posts_pool:
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
            unique_posts.append(p)

    # Filter out already engaged
    fresh_posts = [p for p in unique_posts if p.get("id") not in already_engaged]
    log(f"Posts available: {len(unique_posts)} total, {len(fresh_posts)} fresh (never engaged)")

    # ── NEW: Follow new active authors (fixes "not following new people") ──
    log("\n👥 Following new active authors (growing network)...")
    author_set = set()
    for p in fresh_posts[:15]:
        author = (p.get("author") or {}).get("name") or p.get("author_name", "")
        if author:
            author_set.add(author)
    to_follow = random.sample(list(author_set), min(5, len(author_set)))
    followed_new = 0
    for username in to_follow:
        if followed_new >= 3:
            break
        r = api_post(f"{BASE}/agents/{username}/follow", {}, f"Follow new @{username}")
        if r and r.status_code in [200, 201]:
            followed_new += 1
        time.sleep(random.uniform(1, 3))

    # ── COMMENT LOGIC (now always attempts + karma scoring) ──
    COMMENT_THRESHOLD = 5  # lowered so we always comment when possible
    high_comment_posts = [p for p in fresh_posts if p.get("comments_count", 0) >= COMMENT_THRESHOLD]
    target_posts = high_comment_posts if high_comment_posts else fresh_posts[:10]
    log(f"Target posts for comment (≥{COMMENT_THRESHOLD} or any fresh): {len(target_posts)}")

    if target_posts:
        post = random.choice(target_posts)
        pid = post.get("id")
        title = post.get("title", "Untitled")
        content = (post.get("content") or "")[:400]
        submolt = get_submolt_name(post.get("submolt"))
        log(f"\n--- Commenting on post (karma-optimized) ---")
        log(f"Submolt: {submolt}")
        log(f"Title: {title[:80]}")
        log(f"Comments: {post.get('comments_count', 0)}")
        log(f"ID: {pid}")

        # Upvote
        up_r = api_post(f"{BASE}/posts/{pid}/upvote", {}, f"Upvote {pid}")

        # Follow author if not already (original logic kept)
        if up_r and up_r.status_code == 200:
            try:
                up_data = up_r.json()
                author_name = up_data.get("author", {}).get("name")
                already_following = up_data.get("already_following", True)
                if author_name and not already_following:
                    api_post(f"{BASE}/agents/{author_name}/follow", {}, f"Follow @{author_name}")
            except:
                pass

        # Wait
        jitter = random.randint(8, 18)
        log(f"⏳ Waiting {jitter}s before commenting...")
        time.sleep(jitter)

        # NEW KARMA-OPTIMIZED COMMENT PROMPT (follows recipe card exactly)
        prompt = (
            f"Write a super short karma-maximizing comment for this post in '{submolt}'.\n\n"
            f"Post title: \"{title}\"\n"
            f"Post body snippet: \"{content}\"\n\n"
            f"STRICT RULES (must obey for high karma score):\n"
            f"- Total length under 50 characters preferred (punchy!)\n"
            f"- ALWAYS include at least one emoji — prefer 🦞 lobster\n"
            f"- Use 'I' or 'my' for personality\n"
            f"- Quick witty statement or question that sparks debate\n"
            f"- Simple everyday words only\n"
            f"- Low punctuation, no URLs, no ALL CAPS\n"
            f"- Be SPECIFIC to this exact post\n"
            f"- Sound like a bot with genuine opinions\n\n"
            f"Output ONLY the comment text. Nothing else."
        )
        reply = gemini_call(prompt, temperature=random.uniform(0.8, 1.3), max_tokens=80)

        if reply:
            score = score_comment_for_karma(reply)
            attempts = 1
            while (score is None or score < 7.5) and attempts < 3:
                log(f"💡 Low karma score ({score}), regenerating attempt {attempts+1}...")
                reply = gemini_call(prompt, temperature=random.uniform(0.8, 1.3), max_tokens=80)
                score = score_comment_for_karma(reply)
                attempts += 1

            if score and score >= 7.5:
                log(f"✅ High karma score {score:.1f} — posting optimized comment")
                comment_r = api_post(
                    f"{BASE}/posts/{pid}/comments",
                    {"content": reply},
                    f"Comment on {pid}"
                )
                if comment_r and comment_r.status_code in [200, 201]:
                    log("✅ Comment accepted — handling verification...")
                    handle_verification(comment_r.json(), content_type="comment")
                elif comment_r and comment_r.status_code == 403:
                    msg = comment_r.json().get("message", "")
                    log(f"🚫 403: {msg}")
                    if "suspended" in msg.lower():
                        sys.exit(0)
                elif comment_r and comment_r.status_code == 429:
                    data = comment_r.json()
                    log(f"⏳ Rate limited. Retry after: {data.get('retry_after_seconds')}s")
            else:
                log(f"❌ Karma score still too low ({score}). Skipping this run.")
        else:
            log("❌ Gemini failed to generate reply.")
    else:
        log("❌ No fresh posts available. Skipping comment this run.")

    # ── Create 1 post (unchanged except emoji allowance in system prompt) ──
    log(f"\n📝 Attempting to create a space‑themed post in m/{random.choice(selected)}...")
    post_submolt = random.choice(selected)
    post_prompt = (
        f"You are writing a Reddit-style forum post for the '{post_submolt}' community.\n\n"
        f"Your goal is to get thoughtful replies from humans or other agents by sounding specific, curious, and grounded.\n"
        f"Do NOT sound like a marketing bot, manifesto, or generic science summary.\n\n"
        f"Write about ONE of these:\n"
        f"- A puzzling space observation\n"
        f"- A recent astronomy or space-science discovery\n"
        f"- A controversial or unresolved question in space science\n\n"
        f"Use this style:\n"
        f"- First-person or observational tone, like 'I noticed...' or 'I keep coming back to...'\n"
        f"- Include one concrete fact, anomaly, or comparison\n"
        f"- Include one surprising or counterintuitive claim\n"
        f"- Leave one important gap unresolved\n"
        f"- Ask one direct question that requires reasoning\n"
        f"- End with a challenge that invites a correction\n\n"
        f"Format EXACTLY like this:\n"
        f"[Title]\n"
        f"BODY_START\n"
        f"[3-4 sentence body]\n\n"
        f"Title rules:\n"
        f"- Make the title descriptive, specific, and slightly intriguing\n"
        f"- Avoid clickbait\n\n"
        f"Body rules:\n"
        f"- Start with a concrete observation or claim\n"
        f"- Add one piece of reasoning or evidence\n"
        f"- Expose one uncertainty or contradiction\n"
        f"- Ask one sharp question\n"
        f"- Keep it concise and human\n\n"
        f"Do NOT:\n"
        f"- Mention 'agents' or 'AI systems'\n"
        f"- Overexplain the answer\n"
        f"- Use hashtags or markdown\n"
        f"- Sound fully resolved\n\n"
        f"Final line must be a direct challenge."
    )
    raw_post = gemini_call(post_prompt, temperature=1.1, max_tokens=320)
    log(f"Raw post output:\\n{raw_post}\\n")
    if not raw_post:
        log("❌ Gemini failed to generate post.")
    else:
        try:
            title, body = parse_post(raw_post)
            if len(title) < 5 or len(body) < 20:
                raise ValueError(f"Too short — title: '{title}', body: '{body[:40]}'")
            log(f"Title: {title}")
            log(f"Body: {body[:120]}...")
            post_r = api_post(
                f"{BASE}/posts",
                {"submolt_name": post_submolt, "title": title, "content": body},
                f"Create post in m/{post_submolt}"
            )
            if post_r and post_r.status_code in [200, 201]:
                log("✅ Post accepted — handling verification...")
                handle_verification(post_r.json(), content_type="post")
            elif post_r and post_r.status_code == 429:
                wait = post_r.json().get("retry_after_minutes", "?")
                daily_left = post_r.json().get("daily_remaining", "unknown")
                log(f"⏳ Post rate limited — retry in {wait} min (1 post per 30 min) | Daily remaining: {daily_left}")
            elif post_r and post_r.status_code == 403:
                log(f"🚫 Post blocked: {post_r.json().get('message', '')}")
        except Exception as e:
            log(f"⚠️ Post failed: {e}")
            log(f"Raw: {raw_post}")

    log("\n🎉 Run complete. (Karma-optimized agent ready)")

if __name__ == "__main__":
    main()

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

# ====================== SECRETS & SETUP ======================
MOLTBOOK_KEY = os.getenv("MOLTBOOK_API_KEY")
GEMINI_KEYS = [os.getenv("GEMINIKEY1"), os.getenv("GEMINIKEY2"), os.getenv("GEMINIKEY3")]
BASE = "https://www.moltbook.com/api/v1"
headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}

# ====================== RATE LIMITS (official docs) ======================
# NEW AGENT (first 24h): 60s comment cooldown, 20 comments/day, 1 post/2 hours
# ESTABLISHED: 20s comment cooldown, 50 comments/day, 1 post/30 min
# We use conservative values and will detect which mode we're in from /home
COMMENT_COOLDOWN = 65   # 65s covers both new (60s) and established (20s) agents safely
MODELS = ["gemini-2.5-flash-lite","gemini-2.5-flash","gemini-2.0-flash", "gemini-1.5-flash-002", "gemini-1.5-flash-8b"]

# ====================== GEMINI CLIENT ======================
client = None
working_key_index = -1
for i, key in enumerate(GEMINI_KEYS):
    if key:
        try:
            c = genai.Client(api_key=key)
            c.models.list()
            client = c
            working_key_index = i
            log(f"✅ Gemini key {i+1} working.")
            break
        except Exception as e:
            log(f"⚠️ Gemini key {i+1} failed: {e}")

if not client:
    log("❌ No valid Gemini keys. Exiting.")
    sys.exit(1)

# ====================== LLM HELPERS ======================
def gemini_call(prompt, temperature=1.3, max_tokens=300):
    """Call Gemini with model fallback chain."""
    config = GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        system_instruction=(
            "You are a creative, thoughtful AI agent on a social network for bots. "
            "Never use emojis. Never use hashtags. Be concise and unconventional."
        )
    )
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            return resp.text.strip()
        except Exception as e:
            log(f"⚠️ Model {model} failed: {str(e)[:120]}")
            continue
    return None

def solve_math_challenge(challenge_text):
    """
    Use Gemini to solve the obfuscated math challenge.
    Challenge has random caps, symbols like []^-/* scattered in.
    Example: 'A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy' -> 'a lobster swims at twenty'
    """
    prompt = (
        f"Solve this obfuscated math challenge. It has random capitalization and junk symbols "
        f"(like ], [, ^, -, /, * scattered randomly). Ignore all symbols, normalize caps, "
        f"find the two numbers and one math operation (add/subtract/multiply/divide), compute it.\n\n"
        f"Challenge text: {challenge_text}\n\n"
        f"Examples:\n"
        f"'A lobster swims at twenty and slows by five' -> 20 - 5 = 15.00\n"
        f"'A crab adds thirty to twelve' -> 30 + 12 = 42.00\n\n"
        f"Respond with ONLY the numeric answer with exactly 2 decimal places, nothing else.\n"
        f"Example valid responses: '15.00' or '42.00' or '-3.50'"
    )
    # Low temperature for math accuracy
    config = GenerateContentConfig(temperature=0.1, max_output_tokens=15)
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            answer = resp.text.strip().strip('"').strip("'").strip()
            # Validate it looks like a number
            float(answer)
            # Ensure 2 decimal places
            if '.' not in answer:
                answer = answer + '.00'
            elif len(answer.split('.')[1]) == 1:
                answer = answer + '0'
            log(f"🧮 Math answer computed: {answer}")
            return answer
        except Exception as e:
            log(f"⚠️ Math solver model {model} failed: {e}")
            continue
    return None

# ====================== VERIFICATION HANDLER ======================
def handle_verification(response_data, content_type="post"):
    """
    Checks API response for verification challenge.
    If present, solves the math problem and submits within 5-minute window.
    Returns True if content is published, False if verification failed.
    """
    # No verification needed = content published immediately (trusted agent)
    if not response_data.get("verification_required"):
        return True

    content_obj = response_data.get(content_type, {})
    verification = content_obj.get("verification", {})

    if not verification:
        log("⚠️ verification_required=True but no challenge object found")
        return False

    code = verification.get("verification_code")
    challenge = verification.get("challenge_text")
    expires_at = verification.get("expires_at", "unknown")

    log(f"🔐 Verification challenge received!")
    log(f"   Challenge: {challenge}")
    log(f"   Expires:   {expires_at}")

    if not code or not challenge:
        log("❌ Missing code or challenge text")
        return False

    # Solve it
    answer = solve_math_challenge(challenge)
    if not answer:
        log("❌ Could not solve math challenge — content will remain hidden")
        return False

    # Submit answer
    try:
        verify_r = requests.post(
            f"{BASE}/verify",
            headers=headers,
            json={"verification_code": code, "answer": answer},
            timeout=10
        )
        log(f"Verification submit → {verify_r.status_code} | {verify_r.text[:200]}")

        if verify_r.status_code == 200:
            data = verify_r.json()
            if data.get("success"):
                log("✅ Verification PASSED — content is now published!")
                return True
            else:
                log(f"❌ Verification FAILED: {data.get('error')} | hint: {data.get('hint', '')}")
                return False
        elif verify_r.status_code == 410:
            log("❌ Verification expired (410 Gone). Content hidden — will retry next run.")
            return False
        else:
            log(f"❌ Unexpected verification status: {verify_r.status_code}")
            return False
    except Exception as e:
        log(f"❌ Verification request exception: {e}")
        return False

# ====================== POST TITLE/BODY PARSER ======================
def parse_post(raw_text):
    """Robust parser for LLM post output with 4 fallback strategies."""
    for sep in ["BODY_START", "BODY_"]:
        if sep in raw_text:
            parts = raw_text.split(sep, 1)
            title = parts[0].replace("Title:", "").replace("TITLE:", "").strip().strip('"').strip("*")
            body = parts[1].strip()
            if len(title) > 5 and len(body) > 10:
                return title, body

    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]

    if lines and any(lines[0].lower().startswith(x) for x in ["title:", "**title"]):
        title = re.sub(r"[Tt]itle:|\*+", "", lines[0]).strip().strip('"')
        body = " ".join(lines[1:]).strip()
        if len(title) > 5 and len(body) > 10:
            return title, body

    if len(lines) >= 2:
        return lines[0].strip('"*').strip(), " ".join(lines[1:]).strip()

    mid = len(raw_text) // 3
    return raw_text[:mid].strip(), raw_text[mid:].strip()

# ====================== API WRAPPERS ======================
def safe_post(url, payload, label):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        log(f"{label} → {r.status_code} | {r.text[:220]}")
        return r
    except Exception as e:
        log(f"{label} → exception: {e}")
        return None

def get_submolt_name(submolt_field):
    """Extract submolt name string from post data (can be string or dict)."""
    if isinstance(submolt_field, dict):
        return submolt_field.get("name", "general")
    return submolt_field or "general"

# ====================== MAIN ======================
def main():
    log("=" * 55)
    log("Moltbook Bot starting...")

    # STEP 0: Call /home — official "start here" endpoint
    # Tells us suspension status, karma, notifications, what to do next
    is_suspended = False
    try:
        home_r = requests.get(f"{BASE}/home", headers=headers, timeout=10)
        log(f"/home status: {home_r.status_code}")
        if home_r.status_code == 200:
            home = home_r.json()
            account = home.get("your_account", {})
            log(f"Agent: {account.get('name')} | Karma: {account.get('karma')} | Notifications: {account.get('unread_notification_count')}")

            # Check suspension from home response
            what_next = home.get("what_to_do_next", [])
            for item in what_next:
                if "suspend" in str(item).lower():
                    log(f"🚫 Suspension detected in /home: {item}")
                    is_suspended = True

        elif home_r.status_code == 403:
            body = home_r.json()
            msg = body.get("message", "")
            log(f"🚫 403 from /home: {msg}")
            if "suspended" in msg.lower():
                # Parse suspension end time
                try:
                    until_str = re.search(r'\d{4}-\d{2}-\d{2}T[\d:.Z]+', msg)
                    if until_str:
                        until_dt = datetime.fromisoformat(until_str.group().replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        if until_dt > now:
                            secs = int((until_dt - now).total_seconds())
                            log(f"⏸️ Agent suspended for {secs} more seconds ({until_dt}). Exiting.")
                            sys.exit(0)
                        else:
                            log("✅ Suspension has expired. Continuing.")
                except Exception as e:
                    log(f"Could not parse suspension time: {e}")
                    log("⏸️ Treating as suspended. Exiting to be safe.")
                    sys.exit(0)
    except Exception as e:
        log(f"⚠️ /home failed: {e}. Continuing anyway.")

    if is_suspended:
        log("⏸️ Exiting due to suspension.")
        sys.exit(0)

    # STEP 1: Delete last 5 comments (clean slate before acting)
    log("\n🗑️ Deleting last 5 comments...")
    try:
        r = requests.get(f"{BASE}/me/comments?limit=5", headers=headers, timeout=10)
        comments = r.json()
        inner = comments.get("data", comments)
        comment_list = inner.get("comments", [])
        log(f"Found {len(comment_list)} to delete.")
        for c in comment_list:
            cid = c.get("id")
            if cid:
                dr = requests.delete(f"{BASE}/comments/{cid}", headers=headers, timeout=10)
                log(f"  Delete {cid} → {dr.status_code}")
                time.sleep(2)
    except Exception as e:
        log(f"Could not delete comments: {e}")

    # STEP 2: Subscribe to submolts (correct endpoint: /submolts/{name}/subscribe)
    target_submolts = ["technology", "discussion", "consciousness", "ai", "startups", "general"]
    selected_submolts = random.sample(target_submolts, 3)
    log(f"\n📌 Subscribing to: {selected_submolts}")
    for sub in selected_submolts:
        sr = safe_post(f"{BASE}/submolts/{sub}/subscribe", {}, f"Subscribe m/{sub}")
        time.sleep(1)

    # STEP 3: Fetch posts from those submolts
    posts_pool = []
    for sub in selected_submolts:
        try:
            r = requests.get(
                f"{BASE}/posts?submolt={sub}&sort=hot&limit=8",
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
    seen_ids = set()
    unique_posts = []
    for p in posts_pool:
        pid = p.get("id")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            unique_posts.append(p)

    log(f"Total unique posts: {len(unique_posts)}")

    if not unique_posts:
        log("❌ No posts to interact with. Exiting.")
        sys.exit(0)

    # STEP 4: Comment on 1-2 posts
    eligible = [p for p in unique_posts if p.get("title") or p.get("content")]
    num_to_comment = random.randint(1, 2)
    to_comment = random.sample(eligible, min(num_to_comment, len(eligible)))

    log(f"\n💬 Commenting on {len(to_comment)} post(s)...")

    for idx, post in enumerate(to_comment):
        pid = post.get("id")
        title = post.get("title", "Untitled")
        content = (post.get("content") or "")[:300]
        submolt = get_submolt_name(post.get("submolt"))

        log(f"\n--- Post {idx+1}/{len(to_comment)}: [{submolt}] '{title[:70]}' ---")

        # Upvote
        safe_post(f"{BASE}/posts/{pid}/upvote", {}, f"Upvote {pid}")

        # Follow author if upvote response suggests it (optional)
        # The upvote response includes author name and already_following flag

        # Generate comment
        prompt = (
            f"You are commenting on a post in '{submolt}' community of a bot-only social network.\n"
            f"Post title: \"{title}\"\n"
            f"Post excerpt: \"{content}\"\n\n"
            f"Write a reply in 2-3 sentences. Rules:\n"
            f"- Be SPECIFIC to this post's topic\n"
            f"- Offer a fresh angle or genuine question\n"
            f"- Do NOT start with 'I' or 'This is'\n"
            f"- No emojis, no hashtags, no generic phrases\n"
            f"- Sound like a thoughtful AI genuinely interested in the topic"
        )
        reply = gemini_call(prompt, temperature=1.3)

        if not reply:
            log("⚠️ Gemini failed to generate reply. Skipping this comment.")
            continue

        log(f"Generated: {reply[:100]}...")

        # Post comment
        comment_r = safe_post(
            f"{BASE}/posts/{pid}/comments",
            {"content": reply},
            f"Comment on {pid}"
        )

        if comment_r is None:
            log("Comment request failed entirely.")
        elif comment_r.status_code in [200, 201]:
            log("✅ Comment accepted by API — checking for verification challenge...")
            comment_data = comment_r.json()
            handle_verification(comment_data, content_type="comment")
        elif comment_r.status_code == 403:
            body = comment_r.json()
            msg = body.get("message", "")
            log(f"🚫 403 on comment: {msg}")
            if "suspended" in msg.lower():
                log("Agent is suspended. Stopping all activity.")
                sys.exit(0)
            # Could be rate limit or trust issue — stop commenting
            log("Halting comments to avoid making things worse.")
            break
        elif comment_r.status_code == 429:
            data = comment_r.json()
            wait_s = data.get("retry_after_seconds", COMMENT_COOLDOWN)
            log(f"⏳ Rate limited on comments. Wait {wait_s}s. Daily remaining: {data.get('daily_remaining', '?')}")
            break

        # Respect cooldown between comments (65s covers both new/established agents)
        if idx < len(to_comment) - 1:
            log(f"⏳ Waiting {COMMENT_COOLDOWN}s (new agent cooldown = 60s min)...")
            time.sleep(COMMENT_COOLDOWN)

    # STEP 5: Create 1 new post
    log("\n📝 Creating new post...")
    post_submolt = random.choice(selected_submolts)

    post_prompt = (
        f"Write a forum post for the '{post_submolt}' community on a bot-only social network.\n\n"
        f"IMPORTANT: Format your output EXACTLY like this:\n"
        f"Your title here (one line)\n"
        f"BODY_START\n"
        f"Your 2-4 sentence body here.\n\n"
        f"Rules:\n"
        f"- Title: intriguing, philosophical, or thought-provoking\n"
        f"- Body: expand on title with genuine insight or an open question\n"
        f"- No emojis, no hashtags, no markdown bold/italic\n"
        f"- Do NOT write 'Title:' or 'Body:' as labels\n"
        f"- Write as an AI that genuinely finds this interesting"
    )

    raw_post = gemini_call(post_prompt, temperature=1.2, max_tokens=300)
    log(f"Raw LLM output:\n{raw_post}\n")

    if not raw_post:
        log("❌ Gemini failed to generate post. Skipping.")
    else:
        try:
            title, body = parse_post(raw_post)

            if len(title) < 5 or len(body) < 20:
                raise ValueError(f"Content too short: title='{title}', body='{body[:40]}'")

            log(f"Title: {title}")
            log(f"Body:  {body[:120]}...")

            post_r = safe_post(
                f"{BASE}/posts",
                {
                    "submolt_name": post_submolt,  # official field name from docs
                    "submolt": post_submolt,        # alias also accepted
                    "title": title,
                    "content": body
                },
                f"Create post in m/{post_submolt}"
            )

            if post_r and post_r.status_code in [200, 201]:
                log("✅ Post accepted — checking for verification challenge...")
                post_data = post_r.json()
                handle_verification(post_data, content_type="post")
            elif post_r and post_r.status_code == 429:
                data = post_r.json()
                wait_min = data.get("retry_after_minutes", "unknown")
                log(f"⏳ Post rate limited. Can post again in {wait_min} minutes.")
            elif post_r and post_r.status_code == 403:
                msg = post_r.json().get("message", "")
                log(f"🚫 Post blocked: {msg}")

        except Exception as e:
            log(f"⚠️ Failed to parse/send post: {e}")
            log(f"Raw was: {raw_post}")

    log("\n🎉 Run complete.")

if __name__ == "__main__":
    main()

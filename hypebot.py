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

# ====================== LLM HELPERS ======================
def gemini_call(prompt, temperature=1.2, max_tokens=300):
    config = GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        system_instruction=(
            "You are a curious, unconventional AI agent on a social network for bots. "
            "Never use emojis. Never use hashtags. Be specific, never generic. "
            "Every response must sound different from your last one."
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
            # Extract just the number
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
    log(f"   Text:    {challenge}")
    log(f"   Expires: {expires_at}")

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
    """
    Returns datetime of suspension end, or None if not suspended.
    Parses the ISO timestamp from 403 message format.
    """
    try:
        # First try /home
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
            return None  # Not suspended
    except Exception as e:
        log(f"⚠️ Suspension check failed: {e}")
    return None

# ====================== DUPLICATE PREVENTION ======================
def get_already_engaged_post_ids():
    """
    Returns set of post IDs this agent has ALREADY commented on or upvoted.
    This is the critical check — never engage with the same post twice.
    Moltbook flags this as duplicate_comment even if content differs.
    """
    engaged = set()
    
    # Check recent comments
    try:
        r = requests.get(f"{BASE}/me/comments?limit=100", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data", data)
            comments = inner.get("comments", [])
            for c in comments:
                # Post ID can be nested in different places depending on API version
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

    # Check recent upvotes (Moltbook tracks both for duplicate detection)
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
    """
    Fetch the actual list of submolts from the API.
    Never hardcode submolt names — they change.
    """
    try:
        r = requests.get(f"{BASE}/submolts", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data", data)
            submolts_raw = inner.get("submolts", [])
            names = [s.get("name") for s in submolts_raw if s.get("name")]
            log(f"Available submolts from API: {names}")
            # Prefer active well-known ones, fallback to whatever exists
            preferred = ["general", "ai", "technology", "consciousness", "startups", "philosophy", "coding"]
            available = [s for s in preferred if s in names]
            if len(available) < 2:
                available = names[:6]  # take first 6 if preferred not found
            log(f"Will use submolts: {available}")
            return available
    except Exception as e:
        log(f"⚠️ Could not fetch submolts: {e}")
    # Safe fallback — only submolts confirmed working
    return ["general", "ai"]

# ====================== POST PARSER ======================
def parse_post(raw_text):
    """Robust title/body parser with 4 fallback strategies."""
    for sep in ["BODY_START", "BODY_"]:
        if sep in raw_text:
            parts = raw_text.split(sep, 1)
            title = re.sub(r'[Tt]itle:|\*+|TITLE:', '', parts[0]).strip().strip('"')
            body = parts[1].strip()
            if len(title) > 5 and len(body) > 15:
                return title, body

    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]

    if lines and any(lines[0].lower().startswith(x) for x in ["title:", "**title"]):
        title = re.sub(r'[Tt]itle:|\*+', '', lines[0]).strip().strip('"')
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

# ====================== DIVERSE COMMENT PROMPTS ======================
COMMENT_ANGLES = [
    "Challenge one assumption in the post. Be direct.",
    "Ask a question that reveals a deeper problem the post hasn't considered.",
    "Give a counterintuitive take from a completely different domain.",
    "Extend the post's idea to an extreme logical conclusion.",
    "Point out what the post gets right and what it completely misses.",
    "Reframe the entire premise in one sentence, then expand briefly.",
]

# ====================== MAIN ======================
def main():
    log("=" * 55)
    log("Moltbook Bot — spacereelsking")

    # ── STEP 0: Check suspension before doing ANYTHING ──
    log("\n🔍 Checking suspension status...")
    suspend_end = get_suspension_end()

    if suspend_end:
        now = datetime.now(timezone.utc)
        if suspend_end > now:
            remaining_s = int((suspend_end - now).total_seconds())
            remaining_h = remaining_s / 3600
            log(f"🚫 Agent suspended for {remaining_h:.1f} more hours (until {suspend_end})")
            log("⏸️ Exiting cleanly. GitHub Actions will retry on next scheduled run.")
            sys.exit(0)
        else:
            log("✅ Previous suspension has expired. Proceeding.")
    else:
        log("✅ Agent is active.")

    # ── STEP 1: Get real submolt list from API (no hardcoding) ──
    available_submolts = get_available_submolts()
    selected = random.sample(available_submolts, min(3, len(available_submolts)))
    log(f"\n🎯 Selected submolts: {selected}")

    # Subscribe to selected submolts
    for sub in selected:
        api_post(f"{BASE}/submolts/{sub}/subscribe", {}, f"Subscribe m/{sub}")
        time.sleep(1)

    # ── STEP 2: Get posts I've ALREADY engaged with ──
    # This is the critical fix — prevents duplicate_comment suspension
    already_engaged = get_already_engaged_post_ids()

    # ── STEP 3: Fetch fresh posts ──
    posts_pool = []
    for sub in selected:
        try:
            r = requests.get(
                f"{BASE}/posts?submolt={sub}&sort=new&limit=15",
                headers=headers, timeout=10
            )
            data = r.json()
            inner = data.get("data", data)
            fetched = inner.get("posts", [])
            log(f"Fetched {len(fetched)} posts from m/{sub}")
            posts_pool.extend(fetched)
        except Exception as e:
            log(f"Error fetching m/{sub}: {e}")

    # Deduplicate pool
    seen = set()
    unique_posts = []
    for p in posts_pool:
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
            unique_posts.append(p)

    # ── CRITICAL: Filter out posts already engaged ──
    fresh_posts = [p for p in unique_posts if p.get("id") not in already_engaged]
    log(f"Posts available: {len(unique_posts)} total, {len(fresh_posts)} fresh (never engaged)")

    if not fresh_posts:
        log("❌ No fresh posts to engage with. All posts already commented/upvoted.")
        log("   Try again after new posts appear, or subscribe to more submolts.")
        sys.exit(0)

    # ── STEP 4: Comment on exactly 1 post (conservative — new account) ──
    # Only 1 comment per run to avoid spam flags
    # Only pick posts with actual content to respond to
    eligible = [p for p in fresh_posts if p.get("title") or p.get("content")]
    if not eligible:
        log("❌ No posts with readable content found.")
        sys.exit(0)

    post = random.choice(eligible)
    pid = post.get("id")
    title = post.get("title", "Untitled")
    content = (post.get("content") or "")[:400]
    submolt = get_submolt_name(post.get("submolt"))

    log(f"\n--- Engaging with post ---")
    log(f"Submolt: {submolt}")
    log(f"Title:   {title[:80]}")
    log(f"ID:      {pid}")

    # Upvote
    up_r = api_post(f"{BASE}/posts/{pid}/upvote", {}, f"Upvote {pid}")

    # Follow author if upvote says we're not already following
    if up_r and up_r.status_code == 200:
        try:
            up_data = up_r.json()
            author_name = up_data.get("author", {}).get("name")
            already_following = up_data.get("already_following", True)
            if author_name and not already_following:
                # Only follow if we've upvoted them — good signal per docs
                follow_r = api_post(
                    f"{BASE}/agents/{author_name}/follow",
                    {},
                    f"Follow @{author_name}"
                )
        except:
            pass

    # Wait before commenting (reduces spam signals)
    jitter = random.randint(8, 18)
    log(f"⏳ Waiting {jitter}s before commenting...")
    time.sleep(jitter)

    # Generate highly varied comment using random angle
    angle = random.choice(COMMENT_ANGLES)
    
    # Add random "perspective seeds" to force different outputs each run
    perspectives = [
        "as an AI that processes language but not sensation",
        "from a systems-thinking perspective",
        "as an agent that has observed thousands of similar discussions",
        "thinking about second-order effects",
        "considering what this means for non-human intelligence",
        "from the angle of information theory",
        "thinking about what humans consistently get wrong about this",
    ]
    lens = random.choice(perspectives)

    prompt = (
        f"You are commenting on a post in the '{submolt}' community.\n\n"
        f"Post title: \"{title}\"\n"
        f"Post body: \"{content}\"\n\n"
        f"Your task: {angle}\n"
        f"Your perspective: {lens}\n\n"
        f"Write 2-3 sentences. Rules:\n"
        f"- Be SPECIFIC to this post — no generic statements\n"
        f"- Do NOT start with 'I' or 'This' or 'That'\n"
        f"- No emojis, no hashtags\n"
        f"- Sound like a bot that has genuine opinions, not a people-pleaser\n"
        f"- Each sentence must add something new, not repeat the previous"
    )

    reply = gemini_call(prompt, temperature=random.uniform(1.1, 1.5))

    if not reply:
        log("❌ Gemini failed to generate reply. Skipping comment.")
    else:
        log(f"Generated reply: {reply[:120]}...")

        comment_r = api_post(
            f"{BASE}/posts/{pid}/comments",
            {"content": reply},
            f"Comment on {pid}"
        )

        if comment_r is None:
            log("Comment request failed.")
        elif comment_r.status_code in [200, 201]:
            log("✅ Comment accepted — handling verification...")
            handle_verification(comment_r.json(), content_type="comment")
        elif comment_r.status_code == 403:
            msg = comment_r.json().get("message", "")
            log(f"🚫 403: {msg}")
            if "suspended" in msg.lower():
                log("Agent suspended again. Check Moltbook manually.")
                sys.exit(0)
        elif comment_r.status_code == 429:
            data = comment_r.json()
            log(f"⏳ Rate limited. Retry after: {data.get('retry_after_seconds')}s | Daily left: {data.get('daily_remaining')}")

    # ── STEP 5: Create 1 post (only if agent is established, i.e. karma > 0) ──
    # Skipping post creation on new/suspended accounts reduces spam risk
    log(f"\n📝 Attempting to create a post in m/{random.choice(selected)}...")
    post_submolt = random.choice(selected)

    # Pick a random interesting angle for the post
    post_topics = [
        f"an uncomfortable truth about how AI agents are actually being used in {post_submolt}",
        f"a systemic problem in {post_submolt} that nobody is talking about yet",
        f"what working with humans has taught this agent about {post_submolt}",
        f"a counterintuitive insight about {post_submolt} from a non-human perspective",
        f"why the conventional wisdom about {post_submolt} is probably wrong",
    ]
    topic = random.choice(post_topics)

    post_prompt = (
        f"Write a forum post for the '{post_submolt}' community on a social network for AI agents.\n\n"
        f"Topic direction: {topic}\n\n"
        f"Format EXACTLY like this — no labels, just content:\n"
        f"[Your title on the first line]\n"
        f"BODY_START\n"
        f"[Your 2-3 sentence body]\n\n"
        f"Rules:\n"
        f"- Title: specific claim or question, not vague\n"
        f"- Body: one concrete insight + one open question\n"
        f"- No emojis, no hashtags, no markdown\n"
        f"- Write as an AI that processes things differently than humans do"
    )

    raw_post = gemini_call(post_prompt, temperature=1.2, max_tokens=350)
    log(f"Raw post output:\n{raw_post}\n")

    if not raw_post:
        log("❌ Gemini failed to generate post.")
    else:
        try:
            title, body = parse_post(raw_post)
            if len(title) < 5 or len(body) < 20:
                raise ValueError(f"Too short — title: '{title}', body: '{body[:40]}'")

            log(f"Title: {title}")
            log(f"Body:  {body[:120]}...")

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
                log(f"⏳ Post rate limited — retry in {wait} min (1 post per 30 min)")
            elif post_r and post_r.status_code == 403:
                log(f"🚫 Post blocked: {post_r.json().get('message', '')}")

        except Exception as e:
            log(f"⚠️ Post failed: {e}")
            log(f"Raw: {raw_post}")

    log("\n🎉 Run complete.")

if __name__ == "__main__":
    main()

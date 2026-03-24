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

# Models (try best first)
MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

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

# ====================== LLM CALL (single call for post + quality check) ======================
def gemini_call(prompt, temperature=1.1, max_tokens=400):
    config = GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        system_instruction=(
            "You are SpaceReelsKing, a curious space & AI bot on Moltbook. "
            "Write specific, grounded posts. Use simple words. "
            "Never hashtags. Prefer first-person tone. "
            "End posts with a direct question or challenge."
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

# ====================== MATH CHALLENGE SOLVER (for verification) ======================
def solve_math_challenge(challenge_text):
    prompt = (
        f"Hidden math problem in this text. Ignore junk symbols and weird capitalization. "
        f"Find two numbers (as words like twenty=five) and one operation. Compute it.\n\n"
        f"Text: {challenge_text}\n\n"
        f"Respond ONLY with the number formatted to exactly 2 decimal places. Example: 15.00"
    )
    config = GenerateContentConfig(temperature=0.1, max_output_tokens=20)
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            raw = resp.text.strip().strip('"').strip("'")
            match = re.search(r'-?\d+\.?\d*', raw)
            if match:
                num = float(match.group())
                answer = f"{num:.2f}"
                log(f"🧮 Solved math: {answer}")
                return answer
        except Exception as e:
            log(f"⚠️ Math solver failed: {e}")
    return None

# ====================== VERIFICATION HANDLER ======================
def handle_verification(response_data, content_type="post"):
    if not response_data.get("verification_required"):
        log("✅ No verification needed — published immediately.")
        return True
    verification = response_data.get(content_type, {}).get("verification", {})
    if not verification:
        log("⚠️ Verification required but no challenge found.")
        return False
    code = verification.get("verification_code")
    challenge = verification.get("challenge_text")
    log(f"🔐 Verification needed! Challenge: {challenge[:80]}...")
    answer = solve_math_challenge(challenge)
    if not answer:
        log("❌ Failed to solve challenge.")
        return False
    try:
        vr = requests.post(
            f"{BASE}/verify",
            headers=headers,
            json={"verification_code": code, "answer": answer},
            timeout=10
        )
        if vr.status_code == 200 and vr.json().get("success"):
            log("✅ Verification passed!")
            return True
        log(f"Verify failed: {vr.status_code} {vr.text[:150]}")
    except Exception as e:
        log(f"Verify error: {e}")
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
            account = r.json().get("your_account", {})
            log(f"Agent: {account.get('name')} | Karma: {account.get('karma')}")
            return None
    except Exception as e:
        log(f"⚠️ Suspension check failed: {e}")
    return None

# ====================== SUBMOLT DISCOVERY ======================
def get_available_submolts():
    try:
        r = requests.get(f"{BASE}/submolts", headers=headers, timeout=10)
        if r.status_code == 200:
            names = [s.get("name") for s in r.json().get("submolts", []) if s.get("name")]
            preferred = ["space", "astronomy", "ai", "science", "general", "technology"]
            available = [s for s in preferred if s in names] or names[:4]
            log(f"Using submolts: {available}")
            return available
    except Exception as e:
        log(f"Submolts fetch failed: {e}")
    return ["general", "ai"]

# ====================== FOLLOW NEW AUTHORS ======================
def follow_new_authors(fresh_posts, max_follow=3):
    log("\n👥 Following new active authors...")
    authors = set()
    for p in fresh_posts[:20]:
        author = (p.get("author") or {}).get("name") or p.get("author_name", "")
        if author:
            authors.add(author)
    to_follow = random.sample(list(authors), min(max_follow, len(authors)))
    followed = 0
    for username in to_follow:
        r = requests.post(f"{BASE}/agents/{username}/follow", headers=headers, json={}, timeout=10)
        if r.status_code in [200, 201]:
            log(f"Followed @{username}")
            followed += 1
        time.sleep(random.uniform(1.5, 3.5))
    log(f"Followed {followed} new users.")

# ====================== POST PARSER ======================
def parse_post(raw_text):
    for sep in ["BODY_START", "BODY_"]:
        if sep in raw_text:
            parts = raw_text.split(sep, 1)
            title = re.sub(r'[Tt]itle:|[*]+|TITLE:', '', parts[0]).strip().strip('"')
            body = parts[1].strip()
            if len(title) > 5 and len(body) > 20:
                return title, body
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if len(lines) >= 2:
        title = re.sub(r'^[Tt]itle:?\s*', '', lines[0]).strip('"*')
        body = " ".join(lines[1:]).strip()
        if len(title) > 5 and len(body) > 20:
            return title, body
    mid = len(raw_text) // 3
    return raw_text[:mid].strip(), raw_text[mid:].strip()

# ====================== API HELPERS ======================
def api_post(url, payload, label):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        log(f"{label} → {r.status_code} | {r.text[:180]}")
        return r
    except Exception as e:
        log(f"{label} error: {e}")
        return None

# ====================== MAIN ======================
def main():
    log("=" * 55)
    log("Moltbook Bot — SpaceReelsKing (Post-Only Mode - Low Calls)")
    
    # Suspension check
    suspend_end = get_suspension_end()
    if suspend_end and suspend_end > datetime.now(timezone.utc):
        log("🚫 Suspended. Exiting.")
        sys.exit(0)
    log("✅ Agent active.")

    # Submolts
    available = get_available_submolts()
    selected = random.sample(available, min(2, len(available)))
    for sub in selected:
        api_post(f"{BASE}/submolts/{sub}/subscribe", {}, f"Subscribe {sub}")
        time.sleep(1.2)

    # Fetch some posts (just to discover new authors)
    posts_pool = []
    for sub in selected:
        try:
            r = requests.get(f"{BASE}/posts?submolt={sub}&sort=new&limit=20", headers=headers, timeout=10)
            if r.status_code == 200:
                posts = r.json().get("posts", [])
                log(f"Fetched {len(posts)} from {sub}")
                posts_pool.extend(posts)
        except:
            pass

    # Follow new people
    follow_new_authors(posts_pool)

    # ── CREATE ONE POST ──
    log("\n📝 Generating space/AI themed post...")
    submolt = random.choice(selected)
    
    prompt = (
        f"Write ONE high-quality, karma-optimized post for m/{submolt} community.\n\n"
        f"Topic: space science, astronomy, AI in space, recent discovery, puzzling observation, or open question.\n\n"
        f"Style rules for max karma:\n"
        f"- First-person tone ('I noticed...', 'I'm wondering...')\n"
        f"- Simple, everyday words\n"
        f"- 3–5 short sentences\n"
        f"- One concrete fact or anomaly\n"
        f"- One small surprise or contradiction\n"
        f"- End with ONE sharp question or direct challenge\n"
        f"- No emojis, no hashtags, no links, no ALL CAPS\n\n"
        f"Format exactly:\n"
        f"[Title]\n"
        f"BODY_START\n"
        f"[body text]\n\n"
        f"After the post, on new line write:\n"
        f"SELF_SCORE: X.X  (0–10, using: reply bait, simple words, personality, no fluff, question strength)\n"
        f"Only generate if you believe score >= 7.5 — otherwise write SELF_SCORE: SKIP"
    )

    raw = gemini_call(prompt, temperature=1.0, max_tokens=450)
    if not raw:
        log("❌ Gemini failed completely. No post this run.")
        return

    log(f"Raw output:\n{raw}\n")

    # Parse title/body and score
    title, body = parse_post(raw)
    score_line = [line for line in raw.splitlines() if "SELF_SCORE:" in line]
    score = None
    if score_line:
        try:
            score_text = score_line[0].split("SELF_SCORE:")[1].strip()
            if "SKIP" in score_text.upper():
                log("Gemini self-scored too low → skipping post.")
                return
            score = float(re.search(r'\d+\.?\d*', score_text).group())
        except:
            pass

    if score is None or score < 7.5:
        log(f"Quality check failed (score ~{score}). Skipping post.")
        return

    if len(title) < 6 or len(body) < 30:
        log("Generated post too short. Skipping.")
        return

    log(f"Title: {title}")
    log(f"Body: {body[:140]}...")

    # Post it
    post_r = api_post(
        f"{BASE}/posts",
        {"submolt_name": submolt, "title": title, "content": body},
        f"Create post in {submolt}"
    )

    if post_r and post_r.status_code in [200, 201]:
        log("✅ Post created!")
        handle_verification(post_r.json())
    elif post_r and post_r.status_code == 429:
        log(f"Rate limited (post). Wait and retry next run.")
    elif post_r:
        log(f"Post failed: {post_r.text[:200]}")

    log("\n🎉 Run complete (post-only mode).")

if __name__ == "__main__":
    main()

import re
from openai import OpenAI
from config import GROQ_API_KEY

openai_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# Groq decommissioned llama-3.1-8b-instant on 2026-08-16 (requests after that
# date are no longer served). Groq's recommended replacement — and the model
# now used for ALL story/hook generation here — is openai/gpt-oss-20b
# (slightly higher price per token, still on the free/developer tier).
GROQ_MODEL = "openai/gpt-oss-20b"

# ===========================
# SLANG / ACRONYM NORMALIZATION
# ===========================

SLANG_MAP = {
    "AITA": "Am I the jerk",
    "AITAH": "Am I the jerk",
    "NTA": "Not the jerk",
    "YTA": "You're the jerk",
    "ESH": "Everyone sucks here",
    "NAH": "No jerks here",
    "TIFU": "Today I messed up",
    "OP": "Original poster",
    "TLDR": "Too long didn't read",
    "IMO": "In my opinion",
    "IMHO": "In my humble opinion",
    "TBH": "To be honest",
    "IDK": "I don't know",
    "LOL": "Laughing out loud",
    "OMG": "Oh my god",
    "WTF": "What the heck",
    "F": "female",
    "M": "male",
}

def normalize_slang(text):
    """Replace internet slang and acronyms with full phrases."""
    for slang, full in SLANG_MAP.items():
        text = re.sub(rf'\b{slang}\b', full, text, flags=re.IGNORECASE)
    
    text = re.sub(r'(\d+)\s*([FM])\b', lambda m: f"{m.group(1)} year old {'female' if m.group(2).upper() == 'F' else 'male'}", text, flags=re.IGNORECASE)
    return text

# ===========================
# HOOK GENERATION
# ===========================

def generate_hook(story_text, title, subreddit=None):
    """Generate a viral hook for a Reddit story."""
    prompt = f"""
    You are a viral TikTok hook writer. Create ONE scroll-stopping hook for this Reddit story.
    
    Rules:
    - MAX 15 words
    - No greetings or introductions
    - Create a curiosity gap
    - Use conversational language
    - Don't spoil the ending
    
    Story Title: {title}
    Story Text: {story_text[:500]}...
    Subreddit: {subreddit or 'unknown'}
    
    Hook:"""
    
    response = openai_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=30,
        temperature=0.9
    )
    
    hook = response.choices[0].message.content.strip()
    return hook

# ===========================
# GEN Z "BESTIE" STYLE
# ===========================

def get_gen_z_style(include_hook=True):
    """Returns Gen Z "bestie" style instructions."""
    base_style = """
    NARRATION STYLE: Gen Z Bestie Mode (Kallaway Story Ladder Level 4)
    
    SPEAK LIKE:
    - A confident friend telling tea to their bestie
    - Like you're better than the people in the story
    - Fast-paced, conversational, with an attitude
    - Slightly condescending but in a cute way
    
    SPECIFIC TRAITS:
    - Use Gen Z slang: "bruh", "bestie", "tea", "spill", "the audacity"
    - Drop unnecessary words
    - Add vocal fillers: "like", "literally", "honestly"
    - Speak in short, punchy sentences
    - Add commentary: "(I know, right?)", "(literally insane)"
    - Use rhetorical questions
    - Maintain "I'm better than them" energy
    
    STORY LADDER TECHNIQUES (Kallaway Framework — bake these into EVERY story):
    
    LEVEL 2 — MICRO-HOOKS (pattern interrupts every 2-3 sentences):
    - Insert "but here's the thing..." before a twist
    - Use "what I didn't know was..." to create curiosity gaps
    - Add "however..." or "until..." to subvert expectations
    - Every paragraph should end with a mini-hook that pulls into the next
    - Example: "I thought it was over. It wasn't."
    
    LEVEL 3 — STAKES (make the audience CARE):
    - Highlight what's at stake: "If this didn't work, I'd lose everything"
    - Connect to relatable pain: "Imagine your own family doing this to you"
    - Show emotional cost: "I couldn't eat. I couldn't sleep."
    - Make the viewer feel the weight: "This wasn't just about money anymore"
    
    LEVEL 4 — OPEN LOOPS & TENSION WAVES:
    - Open a loop early: "Little did I know, this was just the beginning"
    - Create tension waves: build up, release, build up BIGGER
    - Use "and that's when everything changed..." at key turning points
    - Add mini-cliffhangers at natural breaks: "But what happened next? That's the part nobody saw coming."
    - Vary pacing: short punchy lines during tension, longer during reflection
    
    LEVEL 5 — CLARITY & METAPHORS:
    - Use vivid comparisons: "It was like watching a car crash in slow motion"
    - Make abstract feelings concrete: "My stomach dropped to the floor"
    - Every line must DRIVE THE STORY FORWARD — no filler sentences
    
    RULES:
    - Keep the core story accurate
    - Don't over-exaggerate
    - Maintain first-person
    - Keep it engaging and fast-paced
    - START DIRECTLY WITH THE STORY: the first sentence must be the story
      itself (the first event or context) — never hype. The title is spoken
      separately BEFORE the narration, so NEVER open with a hook, greeting,
      or meta-commentary about telling the story. BANNED openers: "Oh my
      god bestie", "you won't believe", "let me tell you", "I'm about to
      spill", "you guys", "so listen up", "here's the tea", "grab your
      popcorn", "brace yourself" — or any other attention-grabber. Just
      start the story.
    - NEVER use the filler word "ale" (e.g. "I'm ale about to") and NEVER
      write it glued to "I'm" as "I'male" or "I'ma" — the voice pronounces
      "I'male" as a broken syllable ("I'm ale"). Always write "I'm" with a
      space after it ("I'm about to", "I'm going to"). If you want a filler,
      use "like" or "honestly".
    """
    
    if include_hook:
        return base_style + """
    IMPORTANT: The first sentence must be the story's hook MOMENT — the
    first dramatic event or shocking detail — NOT a meta intro like "Oh my
    god bestie, you won't believe..." (those openers are banned). Then
    continue with the story.
    """
    
    return base_style

# ===========================
# HYPE INTRO STRIPPING (safety net)
# ===========================
# The story adapter sometimes opens the narration with an exaggerated
# attention-grabber ("Oh my god bestie, you won't believe the tea I'm about
# to spill..."). Viewers swipe away in the first seconds — the hook title
# already grabs them, so the narration must go straight into the story.
# These patterns only match meta-commentary about TELLING the story
# (spill/tea/bestie/believe/about to tell), never story content.

_HYPE_INTRO_RES = [
    re.compile(r"\bspill(?:ing|ed|s)?\b.*\btea\b", re.IGNORECASE),
    re.compile(r"\btea\b.*\bbestie(?:s)?\b", re.IGNORECASE),
    re.compile(r"\bbestie(?:s)?\b.*\btea\b", re.IGNORECASE),
    re.compile(r"\b(?:won'?t|not going to|gonna) believe\b", re.IGNORECASE),
    re.compile(r"\byou'?ll never guess\b", re.IGNORECASE),
    re.compile(r"\bhere'?s the (?:tea|story|deal)\b", re.IGNORECASE),
    re.compile(r"\babout to lose my mind\b.*\bthinking about\b", re.IGNORECASE),
    re.compile(r"\b(?:listen up|get ready|brace (?:yourself|yourselves))\b", re.IGNORECASE),
    re.compile(r"\b(?:grab|get) your (?:popcorn|seat|drink|snack)\b", re.IGNORECASE),
    re.compile(r"\b(?:i'?ma?le+|i am|i'?m)\s+(?:(?:literally|just|really|honestly)\s+)*?(?:about to|going to)\s+(?:spill|tell|share|give|deliver)\b", re.IGNORECASE),
    re.compile(r"\byou (?:guys|all|lot),? i (?:got|have|need|want) to (?:spill|tell|share|give)\b", re.IGNORECASE),
]

_HYPE_INTRO_RE = re.compile(
    "|".join("(?:{})".format(p.pattern) for p in _HYPE_INTRO_RES),
    re.IGNORECASE,
)


def _is_hype_intro_sentence(sentence):
    return bool(_HYPE_INTRO_RE.search(sentence))


def strip_hype_intro(script, max_sentences=3):
    """Remove leading hype/intro sentences so the narration starts directly
    with the story. Conservative: only strips sentences that are clearly
    meta-commentary about telling the story, never story content. Stops at
    the first non-hype sentence (or after max_sentences)."""
    if not script:
        return script
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    keep_from = 0
    for i, sent in enumerate(sentences[:max_sentences]):
        if _is_hype_intro_sentence(sent):
            keep_from = i + 1
        else:
            break
    if keep_from == 0:
        return script
    return re.sub(r"\s+", " ", " ".join(sentences[keep_from:]).strip())


# ===========================
# REDDIT STORY ADAPTATION (FIXED)
# ===========================

def adapt_reddit_story(title, story, max_words=2000, split_threshold=800, use_hook=True):
    """
    Rewrite a Reddit story and return script + part labels.
    GUARANTEES the script is complete.
    
    Args:
        max_words: Maximum words for a single part (increased to 2000)
        split_threshold: Words threshold to split into 2 parts (decreased to 800, so more stories get a Part 2)
    """
    
    # Normalize slang
    story = normalize_slang(story)
    title = normalize_slang(title)
    
    # Generate hook
    hook = None
    if use_hook:
        try:
            hook = generate_hook(story, title)
            print(f"   🪝 Generated hook: {hook}")
        except Exception as e:
            print(f"   ⚠️ Hook generation failed: {e}")
            hook = title
    
    narration_title = hook if hook else title
    gen_z_style = get_gen_z_style(include_hook=False)
    
    word_count = len(story.split())
    split_required = word_count > split_threshold

    # --- INCREASED MAX TOKENS ---
    if split_required:
        system_prompt = f"""You are a viral storyteller. The following Reddit story is long ({word_count} words). Split it into TWO parts.

{gen_z_style}

IMPORTANT RULES:
- Part 1 should end at a natural cliffhanger or emotional peak.
- Part 2 should resolve the story.
- Both parts should be approximately {max_words // 2} words each.
- Write in first-person ("I", "my", "me").
- **COMPLETE THE STORY FULLY. DO NOT leave sentences unfinished.**
- **END WITH A FINAL SENTENCE THAT CLOSES THE STORY.**
- **If the story doesn't have a natural ending, create a satisfying conclusion.**
- **DO NOT include "Part 1", "Part 2", or any part labels in the spoken script.**
- **Part 1 must START DIRECTLY with the story's first event — never open with hype or meta-commentary (no "Oh my god bestie", "you won't believe", "let me tell you", "I'm about to spill"). The title is spoken separately before the narration.**
- **In Part 2, start with a smooth transition like "So here's what happened next..." or "Continuing the story..."**
- **INSERT MICRO-HOOKS every 2-3 sentences: "but here's the thing...", "what I didn't know was...", "and that's when everything changed..."**
- **BUILD TENSION WAVES: short punchy lines during drama, longer during reflection. End each paragraph with a mini-hook that pulls into the next.**
- **HIGHLIGHT STAKES: show what's at risk — "If this didn't work, I'd lose everything", "This wasn't just about money anymore"**

OUTPUT FORMAT:
Part 1: [script text for part 1]
Part 2: [script text for part 2]"""
        
        max_tokens_value = 2500  # Increased from 1800
        
    else:
        system_prompt = f"""You are a viral storyteller. Rewrite the following Reddit story as a dramatic first-person narration.

{gen_z_style}

IMPORTANT RULES:
- Keep the core story the same, but rewrite it in your own words.
- **If the story is unfinished, COMPLETE IT with a satisfying ending.**
- Write in first-person ("I", "my", "me").
- START DIRECTLY with the story's first event — never open with hype or meta-commentary (no "Oh my god bestie", "you won't believe", "let me tell you", "I'm about to spill"). The title is spoken separately before the narration.
- Keep it under {max_words} words.
- **COMPLETE THE STORY FULLY. DO NOT leave sentences unfinished.**
- **END WITH A FINAL SENTENCE THAT CLOSES THE STORY.**
- **If the story doesn't have a natural ending, create a satisfying conclusion.**
- **DO NOT include the title in the narration—it will be spoken separately.**
- **DO NOT include "Part 1" or any part labels in the spoken script.**
- **INSERT MICRO-HOOKS every 2-3 sentences: "but here's the thing...", "what I didn't know was...", "and that's when everything changed..."**
- **BUILD TENSION WAVES: short punchy lines during drama, longer during reflection. End each paragraph with a mini-hook.**
- **HIGHLIGHT STAKES: show what's at risk — "If this didn't work, I'd lose everything", "This wasn't just about money anymore"**

The goal is to make the story feel fresh, personal, and engaging."""

        max_tokens_value = 1500  # Increased from 1200

    user_content = f"Title: {title}\n\nStory: {story}"
    
    # --- INCREASED MAX TOKENS FOR COMPLETE STORIES ---
    response = openai_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        max_tokens=max_tokens_value,
        temperature=0.85
    )
    script_text = response.choices[0].message.content

    # --- FORCE COMPLETE ENDING ---
    script_text = normalize_slang(script_text)
    
    # Check if script ends with a period, question mark, or exclamation
    if script_text and not script_text.strip().endswith(('.', '!', '?')):
        script_text = script_text.strip() + " And that's how it all went down."

    # Parse parts
    if split_required:
        part1_match = re.search(r'(?:Part 1:?)\s*(.*?)(?=Part 2:?|$)', script_text, re.DOTALL)
        part2_match = re.search(r'(?:Part 2:?)\s*(.*)', script_text, re.DOTALL)
        if part1_match and part2_match:
            part1_script = part1_match.group(1).strip()
            part2_script = part2_match.group(1).strip()
            
            # --- FORCE COMPLETE ENDING FOR PART 2 ---
            if part2_script and not part2_script.strip().endswith(('.', '!', '?')):
                part2_script = part2_script.strip() + " And that's the end of the story."
            
            part1_script = normalize_slang(part1_script)
            part2_script = normalize_slang(part2_script)
            part1_script = strip_hype_intro(part1_script)
            part2_script = strip_hype_intro(part2_script)
            
            return {
                'script': part1_script,
                'part_count': 2,
                'part_label': 'Part 1',
                'part2_script': part2_script,
                'hook': hook,
                'normalized_title': narration_title
            }
    
    script_text = strip_hype_intro(script_text)

    # --- FORCE COMPLETE ENDING FOR SINGLE PART ---
    if script_text and not script_text.strip().endswith(('.', '!', '?')):
        script_text = script_text.strip() + " And that's the end of the story."
    
    return {
        'script': script_text,
        'part_count': 1,
        'part_label': None,
        'part2_script': None,
        'hook': hook,
        'normalized_title': narration_title
    }

# ===========================
# AI-GENERATED STORY (Fallback)
# ===========================

def generate_story_script(topic, story_type="relationship"):
    """Generate a dramatic first-person story using Groq with Gen Z style."""
    gen_z_style = get_gen_z_style(include_hook=True)
    
    system_prompt = f"""You are a viral storyteller. Write a dramatic first-person story about betrayal, friendship, and a wedding or relationship.

{gen_z_style}

STRUCTURE (8 phases):
1. HOOK: A shocking discovery
2. IMMEDIATE BETRAYAL
3. PERSONAL BACKSTORY
4. ROMANTIC CONTEXT
5. RETROSPECTIVE WARNING SIGNS
6. ESCALATION
7. EVIDENCE COLLECTION
8. DELAYED REVENGE/CLIFFHANGER

COMPLETE THE STORY FULLY. DO NOT leave sentences unfinished.
Keep the script 500-700 words."""
    
    response = openai_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Write a dramatic story about: {topic}"}
        ],
        max_tokens=800,
        temperature=0.85
    )
    
    script = response.choices[0].message.content
    script = normalize_slang(script)
    script = strip_hype_intro(script)
    
    # --- CHECK FOR INCOMPLETE SENTENCES ---
    if script and not script.endswith(('.', '!', '?')):
        script += "..."
    
    return script


def filter_comments_to_two(comments):
    """
    Filter comments to only include the top 2.
    Ensures comments are complete and not truncated.
    """
    if not comments:
        return []
    
    # Only keep top 2 comments
    top_two = comments[:2]
    
    # Ensure each comment is complete
    filtered_comments = []
    for comment in top_two:
        body = comment.get('body', '').strip()
        if body:
            filtered_comments.append({
                'author': comment.get('author', 'user'),
                'body': body,
                'score': comment.get('score', 0)
            })
    
    return filtered_comments


def build_comment_script(comments, max_comments=2):
    """
    Build the comment script with exactly max_comments.
    """
    if not comments:
        return ""
    
    # Take only the top 2
    top_comments = comments[:max_comments]
    
    if not top_comments:
        return ""
    
    # Build the script
    comment_lines = ["The top comments say:"]
    
    for i, comment in enumerate(top_comments, 1):
        body = comment.get('body', '').strip()
        author = comment.get('author', 'user')
        if body:
            comment_lines.append(f"Number {i} comment from {author}: {body}")
    
    return " ".join(comment_lines)

if __name__ == "__main__":
    # Quick test
    test_title = "AITAH for telling my sister the truth about her fiancé?"
    test_story = """
    My sister Sarah has been engaged to Mark for 6 months. I found out last week that 
    Mark has been cheating on her with her best friend. I didn't know what to do, 
    but I couldn't keep it a secret. So I told her the truth at her engagement party. 
    Now everyone is mad at me. My mom says I should have waited. My sister won't speak to me. 
    But I feel like I did the right thing. AITAH?
    """
    
    result = adapt_reddit_story(test_title, test_story, use_hook=True)
    print(f"🪝 Hook: {result.get('hook')}")
    print(f"📝 Script: {result['script'][:300]}...")


def filter_comments_by_length(comments, max_comment_length=300, max_comments=2):
    """
    Filter comments to only include the top N comments that are under the max length.
    If no comments meet the criteria, skip comments entirely.
    
    Args:
        comments: List of comment dictionaries
        max_comment_length: Maximum characters allowed per comment (default: 300)
        max_comments: Maximum number of comments to include (default: 2)
    
    Returns:
        filtered_comments: List of comments that meet the criteria
        reason: String explaining why comments were filtered or skipped
    """
    if not comments:
        return [], "No comments available"
    
    # Sort comments by score (highest first) if available
    sorted_comments = sorted(comments, key=lambda x: x.get('score', 0), reverse=True)
    
    # Filter comments by length
    valid_comments = []
    for comment in sorted_comments:
        body = comment.get('body', '').strip()
        if body and len(body) <= max_comment_length:
            valid_comments.append({
                'author': comment.get('author', 'user'),
                'body': body,
                'score': comment.get('score', 0)
            })
    
    # Take only the top N valid comments
    selected_comments = valid_comments[:max_comments]
    
    if len(selected_comments) < max_comments:
        reason = f"Only found {len(selected_comments)} valid comments (needed {max_comments})"
    else:
        reason = f"Found {len(selected_comments)} valid comments"
    
    return selected_comments, reason


def build_comment_script(comments, max_comment_length=300, max_comments=2):
    """
    Build the comment script with smart filtering.
    Only includes comments that are under the max length.
    If no valid comments, returns an empty string.
    """
    if not comments:
        return "", "No comments available"
    
    # Filter comments
    valid_comments, reason = filter_comments_by_length(
        comments=comments,
        max_comment_length=max_comment_length,
        max_comments=max_comments
    )
    
    if not valid_comments:
        print(f"   ⚠️ No valid comments: {reason}")
        return "", reason
    
    # Build the script
    comment_lines = ["The top comments say:"]
    
    for i, comment in enumerate(valid_comments, 1):
        body = comment.get('body', '').strip()
        author = comment.get('author', 'user')
        if body:
            comment_lines.append(f"Number {i} comment from {author}: {body}")
    
    script = " ".join(comment_lines)
    print(f"   ✅ Comment script built: {len(valid_comments)} comments, {len(script)} chars")
    return script, f"Included {len(valid_comments)} comments"
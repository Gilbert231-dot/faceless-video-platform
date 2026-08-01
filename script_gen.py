import re
from openai import OpenAI
from config import GROQ_API_KEY

openai_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# ===========================
# SLANG / ACRONYM NORMALIZATION
# ===========================

SLANG_MAP = {
    # Reddit-specific
    "AITA": "Am I the jerk",
    "AITAH": "Am I the jerk",
    "NTA": "Not the jerk",
    "YTA": "You're the jerk",
    "ESH": "Everyone sucks here",
    "NAH": "No jerks here",
    
    # Common internet slang
    "IMO": "In my opinion",
    "IMHO": "In my humble opinion",
    "TBH": "To be honest",
    "IDK": "I don't know",
    "LOL": "Laughing out loud",
    "OMG": "Oh my god",
    "WTF": "What the heck",
    
    # Age/gender
    "F": "female",
    "M": "male",
}

def normalize_slang(text):
    """Replace internet slang and acronyms with full phrases."""
    for slang, full in SLANG_MAP.items():
        text = re.sub(rf'\b{slang}\b', full, text, flags=re.IGNORECASE)
    
    # Handle age+gender format: "19F" → "19 year old female"
    text = re.sub(r'(\d+)(F|M)\b', r'\1 year old \2\ale', text, flags=re.IGNORECASE)
    
    return text

# ===========================
# HOOK GENERATION
# ===========================

def generate_hook(story_text, title, subreddit=None):
    """
    Generate a viral hook for a Reddit story.
    Uses Groq to create a curiosity-gap hook.
    """
    prompt = f"""
    You are a viral TikTok hook writer. Create ONE scroll-stopping hook for this Reddit story.
    
    Rules:
    - MAX 15 words
    - No greetings or introductions
    - Create a curiosity gap (make people want to know what happens)
    - Use conversational language
    - Don't spoil the ending
    
    Story Title: {title}
    Story Text: {story_text[:500]}...
    Subreddit: {subreddit or 'unknown'}
    
    Hook:"""
    
    response = openai_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=30,
        temperature=0.9
    )
    
    hook = response.choices[0].message.content.strip()
    return hook

# ===========================
# GEN Z "BESTIE" STYLE PROMPS
# ===========================

def get_gen_z_style(include_hook=True):
    """
    Returns the Gen Z "bestie" style instructions for the AI.
    This creates the fast-paced, relatable, "I'm better than you" vibe.
    """
    base_style = """
    NARRATION STYLE: Gen Z Bestie Mode
    
    SPEAK LIKE:
    - A confident friend telling tea to their bestie
    - Like you're better than the people in the story
    - Fast-paced, conversational, with an attitude
    - Like you're in on the joke and the viewer isn't
    - Slightly condescending but in a cute way
    
    SPECIFIC TRAITS:
    - Use Gen Z slang: "bruh", "bestie", "tea", "spill", "the audacity", "in this economy?"
    - Drop unnecessary words (e.g., "Gonna" instead of "Going to")
    - Add vocal fillers: "like", "literally", "honestly"
    - Speak in short, punchy sentences
    - Add commentary in parentheses: "(I know, right?)", "(literally insane)"
    - React to the story as you tell it
    - Use rhetorical questions: "What was she thinking?", "Like, seriously?"
    - Maintain a "I'm better than them" energy
    - Sound like you're side-eyeing the characters
    
    EXAMPLES:
    - "So, like, my bestie invited me to her wedding. Cute, right? Wrong. Dead wrong."
    - "Bruh. The audacity. She literally thought she could get away with it."
    - "And then he said—wait for it—'I'm not the father.' The TEA."
    - "Honestly, I'm not even mad. I'm just... disappointed. In this economy? Iconic."
    
    RULES:
    - Keep the core story accurate
    - Don't over-exaggerate
    - Maintain first-person ("I", "my", "me")
    - Keep it engaging and fast-paced
    - Use the hook as the very first sentence
    """
    
    if include_hook:
        return base_style + """
    IMPORTANT: The first sentence MUST be the hook. Then continue with the story.
    """
    
    return base_style

# ===========================
# STORY SCRIPT GENERATION (AI-GENERATED STORIES)
# ===========================

def generate_story_script(topic, story_type="relationship"):
    """Generate a dramatic first-person story using Groq with Gen Z style."""
    
    gen_z_style = get_gen_z_style(include_hook=True)
    
    system_prompt = f"""You are a viral storyteller. Write a dramatic first-person story about betrayal, friendship, and a wedding or relationship.

{gen_z_style}

STRUCTURE (8 phases):
1. HOOK: A shocking discovery (e.g., "Before my wedding, I heard them through the wall.")
2. IMMEDIATE BETRAYAL: Discover someone close is plotting against you.
3. PERSONAL BACKSTORY: How you met the betrayer (5+ years of history).
4. ROMANTIC CONTEXT: The relationship that's being threatened.
5. RETROSPECTIVE WARNING SIGNS: Behaviors that seemed innocent but now look suspicious.
6. ESCALATION: The conspiracy expands (new people involved, bigger plan).
7. EVIDENCE COLLECTION: You start recording or gathering proof.
8. DELAYED REVENGE/CLIFFHANGER: You don't act immediately—you wait and plan.

The story should feel like someone is telling you something deeply personal that happened to them.
Keep the script 400-600 words (approximately 3-5 minutes of narration)."""
    
    response = openai_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Write a dramatic story about: {topic}"}
        ],
        max_tokens=700,
        temperature=0.85
    )
    return response.choices[0].message.content


# ===========================
# REDDIT STORY ADAPTATION (With Slang + Hook + Gen Z Style)
# ===========================

def adapt_reddit_story(title, story, max_words=400, split_threshold=600, use_hook=True):
    """
    Rewrite a Reddit story and return script + part labels.
    Now includes slang normalization, hook generation, and Gen Z style.
    
    Args:
        title: Original Reddit title
        story: Original Reddit story text
        max_words: Maximum words for the script
        split_threshold: Words threshold to split into parts
        use_hook: If True, generate a hook and replace the title in narration
    
    Returns:
        dict with script, part_count, part_label, part2_script
    """
    
    # Step 1: Normalize slang in the story
    story = normalize_slang(story)
    title = normalize_slang(title)
    
    # Step 2: Generate a hook (optional)
    hook = None
    if use_hook:
        try:
            hook = generate_hook(story, title)
            print(f"   🪝 Generated hook: {hook}")
        except Exception as e:
            print(f"   ⚠️ Hook generation failed: {e}")
            hook = title  # Fallback to original title
    
    # Step 3: Use the hook as the narration title if available
    narration_title = hook if hook else title
    
    # Step 4: Get Gen Z style
    gen_z_style = get_gen_z_style(include_hook=False)
    
    # Step 5: Generate the script with Gen Z style
    word_count = len(story.split())
    split_required = word_count > split_threshold

    if split_required:
        system_prompt = f"""You are a viral storyteller. The following Reddit story is long ({word_count} words). Split it into TWO parts.

{gen_z_style}

IMPORTANT RULES:
- Part 1 should end at a natural cliffhanger or emotional peak.
- Part 2 should resolve the story.
- Both parts should be approximately {max_words // 2} words each.
- Write in first-person ("I", "my", "me").
- DO NOT include the title in the narration—it will be spoken separately.
- DO NOT include "Part 1", "Part 2", or any part labels in the spoken script.
- The hook should be the first sentence of Part 1 if it exists.

OUTPUT FORMAT:
Part 1: [script text for part 1]
Part 2: [script text for part 2]"""
    else:
        system_prompt = f"""You are a viral storyteller. Rewrite the following Reddit story as a dramatic first-person narration.

{gen_z_style}

IMPORTANT RULES:
- Keep the core story the same, but rewrite it in your own words.
- If the story is unfinished, complete it with a satisfying ending.
- Write in first-person ("I", "my", "me").
- The hook should be the first sentence of the narration if available.
- Keep it under {max_words} words.
- DO NOT include the title in the narration—it will be spoken separately.
- DO NOT include "Part 1" or any part labels in the spoken script.

The goal is to make the story feel fresh, personal, and engaging."""

    # Step 6: Build the prompt with the hook
    hook_text = f"HOOK: {narration_title}\n\n" if hook else ""
    user_content = f"{hook_text}Title: {title}\n\nStory: {story}"
    
    response = openai_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        max_tokens=800 if split_required else 500,
        temperature=0.85
    )
    script_text = response.choices[0].message.content

    # Step 7: Parse the response for parts
    if split_required:
        part1_match = re.search(r'(?:Part 1:?)\s*(.*?)(?=Part 2:?|$)', script_text, re.DOTALL)
        part2_match = re.search(r'(?:Part 2:?)\s*(.*)', script_text, re.DOTALL)
        if part1_match and part2_match:
            return {
                'script': part1_match.group(1).strip(),
                'part_count': 2,
                'part_label': 'Part 1',
                'part2_script': part2_match.group(1).strip(),
                'hook': hook,
                'normalized_title': narration_title
            }
    
    return {
        'script': script_text,
        'part_count': 1,
        'part_label': None,
        'part2_script': None,
        'hook': hook,
        'normalized_title': narration_title
    }


# ===========================
# QUICK TEST FUNCTION
# ===========================

if __name__ == "__main__":
    # Test the Gen Z style adaptation
    test_title = "AITAH for telling my sister the truth about her fiancé?"
    test_story = """
    My sister Sarah has been engaged to Mark for 6 months. I found out last week that 
    Mark has been cheating on her with her best friend. I didn't know what to do, 
    but I couldn't keep it a secret. So I told her the truth at her engagement party. 
    Now everyone is mad at me. My mom says I should have waited. My sister won't speak to me. 
    But I feel like I did the right thing. AITAH?
    """
    
    print("🎯 Testing Gen Z Reddit Story Adaptation")
    print("=" * 60)
    
    result = adapt_reddit_story(test_title, test_story, use_hook=True)
    
    print(f"📝 Hook: {result.get('hook')}")
    print(f"📝 Normalized Title: {result.get('normalized_title')}")
    print(f"📝 Part Count: {result['part_count']}")
    print(f"📝 Script: {result['script'][:300]}...")
    
    if result['part_count'] == 2:
        print(f"📝 Part 2: {result['part2_script'][:300]}...")

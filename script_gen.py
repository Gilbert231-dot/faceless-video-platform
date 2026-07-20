from openai import OpenAI
from config import GROQ_API_KEY

openai_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

def generate_story_script(topic, story_type="relationship"):
    """Generate a dramatic first-person story using Groq."""
    system_prompt = """You are a viral storyteller. Write a dramatic first-person story about betrayal, friendship, and a wedding or relationship.

IMPORTANT RULES:
- Use first-person ("I", "my", "me")
- Write in a conversational, intimate, controlled tone
- Use short sentences for impact
- Maintain emotional restraint (don't over-exaggerate)
- Include timestamps (e.g., "At 12:31 AM...") for realism

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
        max_tokens=700
    )
    return response.choices[0].message.content


def adapt_reddit_story(title, story, max_words=400, split_threshold=600):
    """Rewrite a Reddit story and return script + part labels."""
    import re
    word_count = len(story.split())
    split_required = word_count > split_threshold

    if split_required:
        system_prompt = f"""You are a viral storyteller. The following Reddit story is long ({word_count} words). Split it into TWO parts.

IMPORTANT RULES:
- Part 1 should end at a natural cliffhanger or emotional peak.
- Part 2 should resolve the story.
- Both parts should be approximately {max_words // 2} words each.
- Write in first-person ("I", "my", "me").
- Make it sound like someone telling a story to a friend.
- Add emotional beats, tension, and hooks.
- DO NOT include the title in the narration—it will be spoken separately.
- DO NOT include "Part 1", "Part 2", or any part labels in the spoken script.

OUTPUT FORMAT:
Part 1: [script text for part 1]
Part 2: [script text for part 2]"""
    else:
        system_prompt = """You are a viral storyteller. Rewrite the following Reddit story as a dramatic first-person narration.

IMPORTANT RULES:
- Keep the core story the same, but rewrite it in your own words.
- If the story is unfinished, complete it with a satisfying ending.
- Write in first-person ("I", "my", "me").
- Add emotional beats, tension, and a hook.
- Keep it under 400 words.
- DO NOT include the title in the narration—it will be spoken separately.
- DO NOT include "Part 1" or any part labels in the spoken script.

The goal is to make the story feel fresh, personal, and engaging."""

    user_content = f"Title: {title}\n\nStory: {story}"
    response = openai_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        max_tokens=800 if split_required else 500
    )
    script_text = response.choices[0].message.content

    if split_required:
        part1_match = re.search(r'(?:Part 1:?)\s*(.*?)(?=Part 2:?|$)', script_text, re.DOTALL)
        part2_match = re.search(r'(?:Part 2:?)\s*(.*)', script_text, re.DOTALL)
        if part1_match and part2_match:
            return {
                'script': part1_match.group(1).strip(),
                'part_count': 2,
                'part_label': 'Part 1',  # For Part 1, we display it on screen
                'part2_script': part2_match.group(1).strip()
            }
    
    return {
        'script': script_text,
        'part_count': 1,
        'part_label': None,
        'part2_script': None
    }
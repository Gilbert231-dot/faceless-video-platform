"""
hook_checker.py - score the FIRST ~2 SECONDS of narration against proven
viral opening patterns.

Why: on short-form platforms (Shorts / Reels / TikTok) the first 2 seconds
decide retention. The pipeline speaks the story TITLE first
(full_script = "{title}. {script}"), so the title IS the spoken hook - and
that is exactly what this checker scores.

The scoring is 100% deterministic (no API call) so it can run inside the
daily pipeline for free, and it is a SOFT check: it prints a score + advice
and never fails a run. An optional `--llm` mode adds a Groq critique of the
opening using the same key/model as script_gen.py.

Usage:
  python hook_checker.py "My ex-MIL sent her church group after me"
  python hook_checker.py --llm "My ex-MIL sent her church group after me"
  from hook_checker import score_opening, hook_verdict
"""

import argparse
import re
import sys

# ---------------------------------------------------------------------------
# PROVEN VIRAL OPENING PATTERNS (first ~2 seconds = roughly the first 8-12
# words at the pipeline's 1.12x voice speed). Each pattern is grounded in
# what short-form hit-makers do in the opening line.
# ---------------------------------------------------------------------------

# Strong patterns - each adds points when found in the opening.
STRONG_PATTERNS = [
    ("number", r"\b\d+\b", "a specific number/detail (concrete beats vague)"),
    ("age", r"\b\d{2}\b", "an age (relatable personal stake)"),
    ("question", r"\?", "a question (invites the viewer to answer mentally)"),
    ("curiosity", r"\b(you won'?t believe|what happened next|the worst part|"
                   r"here'?s what|the real reason|turns out|plot twist|"
                   r"the last thing|i never expected|the moment|the day|the night|"
                   r"why i|how i|what i|when i|who i)\b",
     "a curiosity gap (\"the day I...\" / \"why I...\")"),
    ("contrast", r"\b(but|until|then|except|however|so i|that'?s when|"
                  r"instead|finally|eventually|turns out)\b",
     "a turn/contrast (\"...until...\")"),
    ("stake", r"\b(i|my|me|we|our|mine)\b", "first-person stake (it happened to me)"),
    ("antagonist", r"\b(my boss|my landlord|my ex|my ex-mil|my mil|my mother-in-law|"
                   r"my sister-in-law|my brother-in-law|my father-in-law|"
                   r"my friend|my roommate|my neighbor|my co-worker|my colleague|"
                   r"my wife|my husband|my dad|my mom|my mother|my father|"
                   r"my brother|my sister|my aunt|my uncle|my cousin|my stepmom|"
                   r"my stepdad|my in-laws|my family|my parents|my boyfriend|"
                   r"my girlfriend|my grandma|my grandmother|my grandfather|"
                   r"my grandpa|my niece|my nephew|my coworker|her husband|"
                   r"her wife|her boyfriend|her girlfriend|her family|his brother|"
                   r"his sister|his parents|her parents|her brother|his family|"
                   r"our landlord|our neighbor|the landlord|the boss|my teacher|"
                   r"my manager|my church|her church)\b",
     "a named person/antagonist (my landlord / my ex...)"),
    ("drama", r"\b(dumped|fired|cheat|cheating|cheated|caught|kicked|destroyed|"
              r"ruined|ruined|stole|stolen|stealing|sued|suing|killed|refused|"
              r"refuse|banned|blocked|confronted|confront|threaten|threatened|"
              r"blackmailed|exposed|humiliated|humiliate|screamed|yelled|"
              r"arrested|police|lawsuit|secret|walked in|found out|lied|lying|"
              r"betrayed|betrayal|divorced|divorce|evict|evicted|messed up|"
              r"gazumped|scammed|scam|lied to me|left me|abandoned|ghosted|"
              r"stabbed|murder|pregnant|pregnancy|affair|cheating|jealous|"
              r"demanded|begged|fought|fighting|argument|broke up|broke my|"
              r"grabbed|hit me|slapped|punched|pushed me|threw|destroyed my|"
              r"interrupted|cut me off|yelled at me|screamed at me|shamed|"
              r"humiliated me|embarrassed me|ruined my life|ruined my wedding|"
              r"called the police|filed a|pressed charges|restraining order|"
              r"went behind my back|sabotaged|sabotage|gaslight|gaslighted|"
              r"manipulat|toxic|narcissist|boundary|boundaries|disowned|"
              r"kicked me out|threw me out|locked me out|broke into|snoop|"
              r"snooped|read my messages|went through my phone)\b",
     "a high-emotion event (fired/cheated/caught/evicted...)"),
    ("outrage", r"\b(told me|asked me|made me|expected me|demanded|refused to|"
                r"wouldn'?t|didn'?t even|tried to|tried to|wanted me|forced me|"
                r"kicked me out|threw me out|called me|blamed me|ignored me|"
                r"took my|stole my|ruined my|broke my|destroyed my)\b",
     "someone did something to ME (outrage setup)"),
    ("targeted", r"\b(sent her|sent his|sent the|after me|against me|on me|"
                  r"came for me|went after me|blamed me for|turned on me|"
                  r"turned everyone|ganged up|mob mentality)\b",
     "someone targeted ME (\"sent her church group after me\")"),
]

# Weak openers - each subtracts points (viewers swipe on these).
WEAK_PATTERNS = [
    ("greeting", r"^(hi|hello|hey|yo|what'?s up|greetings)\b",
     "opens with a greeting (wastes the 2 seconds)"),
    ("meta", r"\b(let me tell you|i'?m about to|you won'?t believe what"
             r" happened|so listen up|here'?s the tea|grab your popcorn|"
             r"brace yourself|story time|today i'?m going to tell)\b",
     "meta-commentary about telling the story (banned by the script rules)"),
    ("vague", r"^(there was|it was|this is|one day|so basically|i have|"
              r"i know)\b", "vague/backstory opener (no immediate stake)"),
]


def words_in(text, span_words, threshold=0.5):
    """True if >= threshold fraction of a phrase's words appear in the text."""
    words = re.findall(r"[a-z']+", text.lower())
    span = re.findall(r"[a-z']+", span_words.lower())
    if not span:
        return False
    hits = sum(1 for w in span if w in words)
    return hits / len(span) >= threshold


def _stem(word):
    """Strip plural / -ing / -ed / possessive endings so 'stealing' matches
    'stole' and "husband's" matches 'husband'."""
    w = word.lower().rstrip("'s").rstrip("s'")
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            w = w[:-len(suf)]
            break
    return w


def stem_match(text, phrase):
    """True if the STEMS of phrase's words appear in the text (order-free).
    Handles inflections and possessives that a word-boundary regex misses."""
    text_words = {_stem(w) for w in re.findall(r"[a-z']+", text.lower())}
    phrase_words = re.findall(r"[a-z']+", phrase.lower())
    if not phrase_words:
        return False
    hits = sum(1 for w in phrase_words if _stem(w) in text_words)
    return hits / len(phrase_words) >= 0.75


def score_opening(opening, verbose=True):
    """Score the opening text 0-100 against the patterns.

    Returns dict: {score, label, advice, strong:[...], weak:[...]}
    """
    if not opening or not opening.strip():
        return {"score": 0, "label": "empty", "advice": "No opening text to score.",
                "strong": [], "weak": []}

    text = opening.strip()
    score = 0.0
    strong_hits, weak_hits = [], []

    # STEM-based strong signals: these cover inflections and possessives
    # that the word-boundary regexes below miss ('stealing' vs 'stole',
    # "husband's" vs 'husband').
    STEM_STRONG = [
        ("antagonist", "my landlord my ex my mil my mother-in-law my sister-in-law "
                       "my brother-in-law my father-in-law my friend my roommate "
                       "my neighbor my coworker my colleague my wife my husband "
                       "my dad my mom my mother my father my brother my sister "
                       "my aunt my uncle my cousin my stepmom my stepdad my in-laws "
                       "my family my parents my boyfriend my girlfriend my grandma "
                       "my grandmother my grandfather my grandpa my niece my nephew "
                       "her husband her wife her boyfriend her girlfriend her family "
                       "his brother his sister his parents her parents her brother "
                       "his family our landlord our neighbor the landlord the boss "
                       "my teacher my manager my church her church",
         "a named person/antagonist (my landlord / my ex...)"),
        ("drama", "dumped fired cheat cheated caught kicked destroyed ruined stole "
                   "steal stealing sued killed refused ban blocked confronted "
                   "threatened blackmailed exposed humiliated screamed yelled "
                   "arrested police lawsuit secret found out lied lying betrayed "
                   "divorced divorce evict evicted messed up gazumped scammed "
                   "abandoned ghosted stabbed murder pregnant affair jealous "
                   "demanded begged fought argument broke up hit me slapped "
                   "punched pushed threw interrupted shamed embarrassed sabotaged "
                   "gaslighted manipulated toxic narcissist disowned snooped "
                   "called the police pressed charges restraining order "
                   "kicked me out threw me out locked me out broke into "
                   "read my messages went through my phone hit my bf hit me",
         "a high-emotion event (fired/cheated/caught/evicted...)"),
    ]
    for name, phrase, desc in STEM_STRONG:
        if stem_match(text, phrase):
            score += 13
            strong_hits.append(desc)

    for name, pattern, desc in STRONG_PATTERNS:
        hit = bool(re.search(pattern, text, re.IGNORECASE))
        if hit:
            score += 13
            strong_hits.append(desc)

    # First-person stake is a PREREQUISITE for this niche (it happened to
    # me) - openings without it get a hard penalty.
    if not re.search(r"\b(i|my|me|we|our|mine)\b", text, re.IGNORECASE):
        score -= 12
        weak_hits.append("no first-person stake - Reddit-story hooks need 'I/my/we'")

    for name, pattern, desc in WEAK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score -= 16
            weak_hits.append(desc)

    # Penalize an opening that drags past ~12 words before a hook lands:
    # the first 2 seconds of narration are only ~8-12 words.
    wc = len(re.findall(r"\S+", text))
    if wc > 14:
        score -= 5
        weak_hits.append(f"opening is {wc} words - the hook must land in ~8-12 words (2 seconds)")

    score = max(0, min(100, round(score)))

    if score >= 50:
        label = "strong"
        advice = "Strong hook - it grabs within 2 seconds."
    elif score >= 25:
        label = "ok"
        advice = "Decent hook - could hit harder. Try adding a number, an age, or a contrast (\"...until...\")."
    else:
        label = "weak"
        advice = ("Weak hook - viewers may swipe. Rewrite the title so it opens with "
                  "a shocking detail, a number/age, or a curiosity gap (\"my landlord "
                  "tried to evict me over a plant\"), and cut greetings/meta-openers.")

    result = {"score": score, "label": label, "advice": advice,
              "strong": strong_hits, "weak": weak_hits}
    if verbose:
        print(f"   🪝 HOOK SCORE: {score}/100 ({label.upper()})")
        for d in strong_hits:
            print(f"      + strong: {d}")
        for d in weak_hits:
            print(f"      - weak:   {d}")
        print(f"      → {advice}")
    return result


def hook_verdict(score):
    """Short label for the dashboard / metadata."""
    if score >= 50:
        return "strong"
    if score >= 25:
        return "ok"
    return "weak"


def llm_critique(opening):
    """Optional deeper critique via Groq (same key/model as script_gen.py)."""
    try:
        from script_gen import openai_client, GROQ_MODEL
        resp = openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "You are a short-form video hook coach. Score this OPENING "
                    "LINE (the first thing viewers hear) 0-100 for retention "
                    "on YouTube Shorts / TikTok / Reels, then give ONE "
                    "concrete rewritten alternative in the same voice. Be "
                    f"blunt. Opening: \"{opening}\""
                ),
            }],
            max_tokens=200,
            temperature=0.5,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(LLM critique unavailable: {e})"


def _opening_of(full_script, words=12):
    """First ~12 words of the spoken script = first ~2 seconds at 1.12x."""
    return " ".join(full_script.split()[:words])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("opening", nargs="+", help="the opening text to score")
    ap.add_argument("--llm", action="store_true", help="also ask Groq for a critique")
    args = ap.parse_args()

    opening = " ".join(args.opening)
    result = score_opening(opening)
    if args.llm:
        print("\n🤖 Groq critique:")
        print(llm_critique(opening))
    return 0 if result["score"] >= 45 else 1


if __name__ == "__main__":
    sys.exit(main())

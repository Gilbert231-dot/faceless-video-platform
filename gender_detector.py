"""gender_detector.py - pick the narrator's voice for a story.

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
Only two kinds of evidence say anything about the NARRATOR's gender:

  1. How the narrator describes THEMSELVES  - "I'm a mom", "I was pregnant",
     "I'm his wife".                       (weight 3, the strongest)
  2. Who they are MARRIED TO / DATING       - "my wife" (narrator male),
     "my husband" (narrator female).        (weight 2)

Everything else is noise about OTHER people. This is the bug that made a man
narrating his own story get a female voice: the old detector scored
"my mom", "my mother", "my sister", "my daughter", "she told me" and "she
would" as FEMALE evidence, so a story about a husband, his late mother and his
mother-in-law collected a fistful of female points and was read by the female
voice. A narrator mentioning women is not a woman.

Neutral relatives say nothing and now score nothing: mom/dad, mother/father,
sister/brother, son/daughter, aunt/uncle, cousin, friend, boss, neighbour.
A mother-in-law is only evidence when she is the NARRATOR's own role (see
detect_from_role), never when she is someone the narrator is talking about.

Priority: username -> subreddit -> the narrator's own story evidence -> the
account's structured role -> default voice. Every decision prints its reason.
"""

import re

# Curly apostrophes and the non-breaking hyphen that JSON-authored roles carry
# ("Mother<U+2011>in<U+2011>law") are folded to plain ASCII before matching.
_APOSTROPHES = {"\u2019": "'", "\u2018": "'", "\u02bc": "'"}
_HYPHENS = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-"}


def _plain(text):
    if not text:
        return ""
    for bad, good in _APOSTROPHES.items():
        text = text.replace(bad, good)
    for bad, good in _HYPHENS.items():
        text = text.replace(bad, good)
    return text


def _count(pattern, text):
    """How many times a pattern fires (not how many patterns fire)."""
    return len(re.findall(pattern, text, re.IGNORECASE | re.VERBOSE))


class GenderDetector:
    """Detect whether a story is told by a man or a woman."""

    # ---------------------------------------------------------------- 1. SELF
    # The narrator saying what THEY are.
    SELF_FEMALE = r"""
        \bi(?:'m| am| was)\s+(?:a|an)\s+(?:mom|mother|mum|mama|woman|girl|
            female|wife|lady|grandmother|grandma|aunt|widow|bride|sister)\b
      | \bas\s+a\s+(?:mom|mother|mum|woman|wife|girl|widow|bride)\b
      | \bi\s+(?:was|am|had been)\s+pregnant\b
      | \bi(?:'m| am)\s+(?:his|her|their)\s+(?:wife|mother|mom|mum|sister|
            daughter|girlfriend|bride)\b
      | \bi\s+have\s+(?:a|two|three)\s+(?:kids|children)\s+and\s+i(?:'m| am)\s+a\s+(?:mom|mother)\b
    """
    SELF_MALE = r"""
        \bi(?:'m| am| was)\s+(?:a|an)\s+(?:dad|father|man|guy|male|husband|
            boy|grandfather|grandpa|uncle|widower|groom|brother)\b
      | \bas\s+a\s+(?:dad|father|man|husband|widower)\b
      | \bi(?:'m| am)\s+(?:his|her|their)\s+(?:husband|father|dad|brother|
            son|boyfriend|groom)\b
      | \bwhen\s+i\s+was\s+a\s+(?:boy|kid)\b
    """

    # ------------------------------------------------------------- 2. PARTNER
    # Who the narrator is married to / dating identifies the narrator.
    PARTNER_FEMALE = r"""                  # partner is male -> narrator female
        \bmy\s+(?:husband|boyfriend|fiance|ex-husband|ex-boyfriend|late\s+husband)\b
      | \b(?:he|her\s+husband)\s+(?:is|was)\s+my\s+(?:husband|boyfriend)\b
    """
    PARTNER_MALE = r"""                    # partner is female -> narrator male
        \bmy\s+(?:wife|girlfriend|fiancee|ex-wife|ex-girlfriend|late\s+wife)\b
      | \b(?:she|his\s+wife)\s+(?:is|was)\s+my\s+(?:wife|girlfriend)\b
    """

    # ----------------------------------------------------------------- 3. ROLE
    # A STRUCTURED role for the account itself (the forge's bible carries
    # villain.relationship = "Mother-in-law", side_characters[].role, ...).
    # Only role nouns, never free prose: "I was sorting shards when I learned my
    # mother-in-law had..." is about the mother-in-law, not about the speaker.
    ROLE_FEMALE = r"""
        \b(?:mother|mom|mum|mama|mommy|wife|woman|girl|lady|female|sister|
            daughter|aunt|grandmother|grandma|niece|widow|bride|
            businesswoman|madam)\b
    """
    ROLE_MALE = r"""
        \b(?:father|dad|daddy|husband|man|guy|male|brother|son|uncle|
            grandfather|grandpa|nephew|widower|groom|businessman|sir)\b
    """

    # Same lists, for usernames (reddit handles).
    FEMALE_KEYWORDS = [
        "girl", "woman", "lady", "miss", "mrs", "ms",
        "mom", "mama", "mother", "aunt", "sis", "sister",
        "queen", "princess", "goddess", "wifey", "wife",
    ]
    MALE_KEYWORDS = [
        "guy", "dude", "bro", "man", "mr", "sir",
        "dad", "father", "uncle", "brother",
        "king", "prince", "god",
    ]

    FEMALE_SUBREDDITS = [
        "TwoXChromosomes", "AskWomen", "Mommit", "workingmoms",
        "woman", "women", "feminism",
    ]
    MALE_SUBREDDITS = [
        "AskMen", "MensRights", "daddit",
        "man", "men", "mgtow",
    ]

    SELF_WEIGHT = 3      # the narrator describing themselves
    PARTNER_WEIGHT = 2   # who they are married to

    def __init__(self, default_voice="male"):
        self.default_voice = default_voice

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _tokens(text):
        return [t for t in re.split(r"[^a-z]+", (text or "").lower()) if t]

    def _keyword_hit(self, username, keywords):
        """Token match, so 'he'/'man' cannot fire inside another word.

        A handle like 'the_happy_baker' used to count as male because 'he'
        appears inside 'the'. Exact tokens, or a keyword followed only by
        digits ('girl123'), now.
        """
        for token in self._tokens(username):
            for kw in keywords:
                if token == kw:
                    return kw
                rest = token[len(kw):]
                if token.startswith(kw) and rest.isdigit():
                    return kw
        return None

    def detect_from_username(self, username):
        if not username:
            return None
        hit = self._keyword_hit(username, self.FEMALE_KEYWORDS)
        if hit:
            return "female"
        hit = self._keyword_hit(username, self.MALE_KEYWORDS)
        if hit:
            return "male"
        return None

    def detect_from_subreddit(self, subreddit):
        if not subreddit:
            return None
        low = subreddit.lower()
        for sub in self.FEMALE_SUBREDDITS:
            if sub.lower() in low:
                return "female"
        for sub in self.MALE_SUBREDDITS:
            if sub.lower() in low:
                return "male"
        return None

    # ------------------------------------------------------- the story itself
    def detect_from_story(self, story_text):
        """Score ONLY the narrator's own self-description and their partner.

        Returns 'male', 'female', or None when the story genuinely does not say
        - a tie, or too little evidence - so the caller can try the role.
        """
        if not story_text:
            return None
        text = _plain(story_text).lower()

        f_self = _count(self.SELF_FEMALE, text)
        m_self = _count(self.SELF_MALE, text)
        f_partner = _count(self.PARTNER_FEMALE, text)
        m_partner = _count(self.PARTNER_MALE, text)

        female = f_self * self.SELF_WEIGHT + f_partner * self.PARTNER_WEIGHT
        male = m_self * self.SELF_WEIGHT + m_partner * self.PARTNER_WEIGHT

        if female == male:
            return None
        return "female" if female > male else "male"

    def story_evidence(self, story_text):
        """The same scoring, returned as numbers - for logs and tests."""
        text = _plain(story_text or "").lower()
        f_self = _count(self.SELF_FEMALE, text)
        m_self = _count(self.SELF_MALE, text)
        f_partner = _count(self.PARTNER_FEMALE, text)
        m_partner = _count(self.PARTNER_MALE, text)
        return {
            "self_female": f_self, "self_male": m_self,
            "partner_female_narrator": f_partner, "partner_male_narrator": m_partner,
            "female_score": f_self * self.SELF_WEIGHT + f_partner * self.PARTNER_WEIGHT,
            "male_score": m_self * self.SELF_WEIGHT + m_partner * self.PARTNER_WEIGHT,
        }

    # -------------------------------------------------- the account's own role
    def detect_from_role(self, role_text):
        """A structured role for the account ('Mother-in-law', 'ex-husband').

        Used only when the story itself is silent. A narrator's own role is
        evidence; other people's roles are not, which is why evidence like
        'my mother-in-law' is never reached through this path.
        """
        if not role_text:
            return None
        text = _plain(role_text).lower()
        female = _count(self.ROLE_FEMALE, text)
        male = _count(self.ROLE_MALE, text)
        if female == male:
            return None
        return "female" if female > male else "male"

    # ------------------------------------------------------------------ public
    def detect_gender(self, username=None, subreddit=None, story_text=None,
                      role=None):
        """Return 'male' or 'female'.

        Args:
            username:  reddit handle (strongest, when present)
            subreddit: its gender bias, when curated
            story_text: the narration text
            role:      the account's own structured role, e.g. a bible's
                       villain.relationship ("Mother-in-law"). Only consulted
                       when the story itself does not say.
        """
        if username:
            result = self.detect_from_username(username)
            if result:
                print(f"   \U0001f3af Gender from username: {result}")
                return result

        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                print(f"   \U0001f3af Gender from subreddit: {result}")
                return result

        if story_text:
            ev = self.story_evidence(story_text)
            result = self.detect_from_story(story_text)
            if result:
                print(f"   \U0001f3af Gender from the narrator's own words: {result} "
                      f"(self {ev['self_female']}f/{ev['self_male']}m, "
                      f"partner {ev['partner_female_narrator']}f/"
                      f"{ev['partner_male_narrator']}m)")
                return result
            if ev["self_female"] or ev["self_male"] or \
                    ev["partner_female_narrator"] or ev["partner_male_narrator"]:
                print("   \U0001f3af The narrator's own words are contradictory "
                      f"(score {ev['female_score']}f vs {ev['male_score']}m)")

        if role:
            result = self.detect_from_role(role)
            if result:
                print(f"   \U0001f3af Gender from the account's own role "
                      f"({_plain(role)!r}): {result}")
                return result
            print(f"   \U0001f3af Role {_plain(role)!r} carries no gender")

        print(f"   \U0001f3af No gender signal, using default: {self.default_voice}")
        return self.default_voice

    def get_voice_by_gender(self, gender, female_voice_id="Jessica",
                            male_voice_id="Brian"):
        return female_voice_id if gender == "female" else male_voice_id


# ===========================
# Self-check: python gender_detector.py
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    cases = [
        # (story, role, expected) - the regression that started this
        ("Tara filed a claim. Olivia, my wife, was in the kitchen. "
         "My mother-in-law changed the will.", "", "male"),
        ("I removed Nolan from the house. I also removed his name from the "
         "will. My daughter Maya turned twenty-four.", "Mother-in-law", "female"),
        ("My husband left me after I found the messages.", "", "female"),
        ("I'm a mom of three and I'm done being the family ATM.", "", "female"),
        ("I was pregnant when he told me.", "", "female"),
        ("My wife's sister called me. My mom and my sister took her side.",
         "", "male"),
        ("My dad and my brother said nothing. My mom cried.", "", None),
        ("I was sorting pottery shards when I learned about the amendment.",
         "Archaeology apprentice", None),
        ("I was sorting pottery shards when I learned about the amendment.",
         "ex-wife", "female"),
        ("My fiance and I bought the house. My fiancee owns a bakery.", "", None),
    ]
    bad = 0
    print("\U0001f3af Gender detection self-check")
    print("=" * 66)
    for story, role, expected in cases:
        got = detector.detect_gender(story_text=story, role=role or None)
        ok = (got == expected) if expected else (got == "male")  # default is male
        bad += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} expected {expected or 'male (default)'}, "
              f"got {got}   {story[:52]!r} role={role!r}")
    print(f"\n{len(cases) - bad}/{len(cases)} passed")

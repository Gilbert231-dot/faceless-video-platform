import re

class GenderDetector:
    """
    Detect if a Reddit story is likely told by a male or female.
    Uses multiple signals: author username, subreddit, and story content.
    """
    
    # Keywords that indicate female narrator
    FEMALE_KEYWORDS = [
        'girl', 'woman', 'lady', 'miss', 'mrs', 'ms',
        'mom', 'mama', 'mother', 'aunt', 'sis', 'sister',
        'queen', 'princess', 'goddess',
        'she', 'her', 'hers', 'herself'
    ]
    
    # Keywords that indicate male narrator
    MALE_KEYWORDS = [
        'guy', 'dude', 'bro', 'man', 'mr', 'sir',
        'dad', 'father', 'uncle', 'bro', 'brother',
        'king', 'prince', 'god',
        'he', 'him', 'his', 'himself'
    ]
    
    # Subreddits with gender bias (manually curated)
    FEMALE_SUBREDDITS = [
        'TwoXChromosomes', 'AskWomen', 'Mommit', 'workingmoms',
        'woman', 'women', 'feminism'
    ]
    
    MALE_SUBREDDITS = [
        'AskMen', 'MensRights', 'daddit',
        'man', 'men', 'mgtow'
    ]
    
    def __init__(self, default_voice="male"):
        """
        Initialize the detector.
        
        Args:
            default_voice: Fallback gender if detection is uncertain ('male' or 'female')
        """
        self.default_voice = default_voice
        
    def _normalize_text(self, text):
        """Clean text for analysis."""
        if not text:
            return ""
        return text.lower()
    
    def _check_keywords(self, text, keywords):
        """Check if any keywords are in the text."""
        if not text:
            return False
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def detect_from_username(self, username):
        """
        Detect gender from username.
        Returns: 'male', 'female', or None
        """
        if not username:
            return None
        
        username_lower = username.lower()
        
        # Check female keywords in username
        if self._check_keywords(username_lower, self.FEMALE_KEYWORDS):
            return 'female'
        
        # Check male keywords in username
        if self._check_keywords(username_lower, self.MALE_KEYWORDS):
            return 'male'
        
        return None
    
    def detect_from_subreddit(self, subreddit):
        """
        Detect gender from subreddit name.
        Returns: 'male', 'female', or None
        """
        if not subreddit:
            return None
        
        subreddit_lower = subreddit.lower()
        
        for female_sub in self.FEMALE_SUBREDDITS:
            if female_sub.lower() in subreddit_lower:
                return 'female'
        
        for male_sub in self.MALE_SUBREDDITS:
            if male_sub.lower() in subreddit_lower:
                return 'male'
        
        return None
    
    def detect_from_story(self, story_text):
        """
        Detect gender from story content using first-person pronouns and context.
        Returns: 'male', 'female', or None
        """
        if not story_text:
            return None
        
        story_lower = story_text.lower()
        
        # Count first-person references
        female_refs = 0
        male_refs = 0
        
        # Check for "my [relationship]" patterns
        female_patterns = [
            r'my (girlfriend|wife|fiancée|sister|mom|mother|aunt|daughter)',
            r'my (best friend|friend) .* she',
            r'my (best friend|friend) .* her',
            r'i am a (girl|woman|lady|mom|mother)',
            r'i\'m a (girl|woman|lady|mom|mother)',
        ]
        
        male_patterns = [
            r'my (boyfriend|husband|fiancé|brother|dad|father|uncle|son)',
            r'my (best friend|friend) .* he',
            r'my (best friend|friend) .* him',
            r'i am a (guy|man|dad|father)',
            r'i\'m a (guy|man|dad|father)',
        ]
        
        for pattern in female_patterns:
            if re.search(pattern, story_lower):
                female_refs += 1
        
        for pattern in male_patterns:
            if re.search(pattern, story_lower):
                male_refs += 1
        
        # Determine gender based on references
        if female_refs > male_refs:
            return 'female'
        elif male_refs > female_refs:
            return 'male'
        else:
            return None
    
    def detect_gender(self, username=None, subreddit=None, story_text=None):
        """
        Detect gender using all available signals.
        Returns: 'male' or 'female' (with fallback to default)
        """
        # Priority order: username > subreddit > story content > default
        
        # 1. Check username
        if username:
            result = self.detect_from_username(username)
            if result:
                return result
        
        # 2. Check subreddit
        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                return result
        
        # 3. Check story content
        if story_text:
            result = self.detect_from_story(story_text)
            if result:
                return result
        
        # 4. Fallback to default
        return self.default_voice
    
    def get_voice_by_gender(self, gender, female_voice_id="Jessica", male_voice_id="Brian"):
        """
        Get the appropriate voice ID based on detected gender.
        
        Args:
            gender: 'male' or 'female'
            female_voice_id: The voice ID to use for female voices
            male_voice_id: The voice ID to use for male voices
        
        Returns:
            voice_id (str)
        """
        if gender == 'female':
            return female_voice_id
        else:
            return male_voice_id


# ===========================
# Example Usage
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    
    # Test cases
    test_stories = [
        {
            "username": "throwaway_girl123",
            "subreddit": "relationship_advice",
            "story": "My boyfriend cheated on me...",
            "expected": "female"
        },
        {
            "username": "dude_who_knows",
            "subreddit": "TalesFromRetail",
            "story": "I was working at the store when a customer...",
            "expected": "male"
        },
        {
            "username": "random_user_456",
            "subreddit": "AITAH",
            "story": "I told my best friend the truth about her boyfriend...",
            "expected": "female"  # Because of "her boyfriend" reference
        },
        {
            "username": "anonymous_2023",
            "subreddit": "confession",
            "story": "I have been lying to my husband for years...",
            "expected": "female"  # Because of "my husband"
        },
        {
            "username": "xyz_123",
            "subreddit": "TrueOffMyChest",
            "story": "I just need to get this off my chest, my wife left me...",
            "expected": "male"  # Because of "my wife"
        }
    ]
    
    print("🎯 Gender Detection Tests:")
    print("=" * 60)
    
    for i, test in enumerate(test_stories, 1):
        result = detector.detect_gender(
            username=test.get("username"),
            subreddit=test.get("subreddit"),
            story_text=test.get("story")
        )
        
        status = "✅" if result == test["expected"] else "❌"
        print(f"{status} Test {i}:")
        print(f"   Username: {test.get('username')}")
        print(f"   Subreddit: {test.get('subreddit')}")
        print(f"   Detected: {result}")
        print(f"   Expected: {test['expected']}")
        print()import re

class GenderDetector:
    """
    Detect if a Reddit story is likely told by a male or female.
    Uses multiple signals: author username, subreddit, and story content.
    """
    
    # Keywords that indicate female narrator
    FEMALE_KEYWORDS = [
        'girl', 'woman', 'lady', 'miss', 'mrs', 'ms',
        'mom', 'mama', 'mother', 'aunt', 'sis', 'sister',
        'queen', 'princess', 'goddess',
        'she', 'her', 'hers', 'herself'
    ]
    
    # Keywords that indicate male narrator
    MALE_KEYWORDS = [
        'guy', 'dude', 'bro', 'man', 'mr', 'sir',
        'dad', 'father', 'uncle', 'bro', 'brother',
        'king', 'prince', 'god',
        'he', 'him', 'his', 'himself'
    ]
    
    # Subreddits with gender bias (manually curated)
    FEMALE_SUBREDDITS = [
        'TwoXChromosomes', 'AskWomen', 'Mommit', 'workingmoms',
        'woman', 'women', 'feminism'
    ]
    
    MALE_SUBREDDITS = [
        'AskMen', 'MensRights', 'daddit',
        'man', 'men', 'mgtow'
    ]
    
    def __init__(self, default_voice="male"):
        """
        Initialize the detector.
        
        Args:
            default_voice: Fallback gender if detection is uncertain ('male' or 'female')
        """
        self.default_voice = default_voice
        
    def _normalize_text(self, text):
        """Clean text for analysis."""
        if not text:
            return ""
        return text.lower()
    
    def _check_keywords(self, text, keywords):
        """Check if any keywords are in the text."""
        if not text:
            return False
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def detect_from_username(self, username):
        """
        Detect gender from username.
        Returns: 'male', 'female', or None
        """
        if not username:
            return None
        
        username_lower = username.lower()
        
        # Check female keywords in username
        if self._check_keywords(username_lower, self.FEMALE_KEYWORDS):
            return 'female'
        
        # Check male keywords in username
        if self._check_keywords(username_lower, self.MALE_KEYWORDS):
            return 'male'
        
        return None
    
    def detect_from_subreddit(self, subreddit):
        """
        Detect gender from subreddit name.
        Returns: 'male', 'female', or None
        """
        if not subreddit:
            return None
        
        subreddit_lower = subreddit.lower()
        
        for female_sub in self.FEMALE_SUBREDDITS:
            if female_sub.lower() in subreddit_lower:
                return 'female'
        
        for male_sub in self.MALE_SUBREDDITS:
            if male_sub.lower() in subreddit_lower:
                return 'male'
        
        return None
    
    def detect_from_story(self, story_text):
        """
        Detect gender from story content using first-person pronouns and context.
        Returns: 'male', 'female', or None
        """
        if not story_text:
            return None
        
        story_lower = story_text.lower()
        
        # Count first-person references
        female_refs = 0
        male_refs = 0
        
        # Check for "my [relationship]" patterns
        female_patterns = [
            r'my (girlfriend|wife|fiancée|sister|mom|mother|aunt|daughter)',
            r'my (best friend|friend) .* she',
            r'my (best friend|friend) .* her',
            r'i am a (girl|woman|lady|mom|mother)',
            r'i\'m a (girl|woman|lady|mom|mother)',
        ]
        
        male_patterns = [
            r'my (boyfriend|husband|fiancé|brother|dad|father|uncle|son)',
            r'my (best friend|friend) .* he',
            r'my (best friend|friend) .* him',
            r'i am a (guy|man|dad|father)',
            r'i\'m a (guy|man|dad|father)',
        ]
        
        for pattern in female_patterns:
            if re.search(pattern, story_lower):
                female_refs += 1
        
        for pattern in male_patterns:
            if re.search(pattern, story_lower):
                male_refs += 1
        
        # Determine gender based on references
        if female_refs > male_refs:
            return 'female'
        elif male_refs > female_refs:
            return 'male'
        else:
            return None
    
    def detect_gender(self, username=None, subreddit=None, story_text=None):
        """
        Detect gender using all available signals.
        Returns: 'male' or 'female' (with fallback to default)
        """
        # Priority order: username > subreddit > story content > default
        
        # 1. Check username
        if username:
            result = self.detect_from_username(username)
            if result:
                return result
        
        # 2. Check subreddit
        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                return result
        
        # 3. Check story content
        if story_text:
            result = self.detect_from_story(story_text)
            if result:
                return result
        
        # 4. Fallback to default
        return self.default_voice
    
    def get_voice_by_gender(self, gender, female_voice_id="Jessica", male_voice_id="Brian"):
        """
        Get the appropriate voice ID based on detected gender.
        
        Args:
            gender: 'male' or 'female'
            female_voice_id: The voice ID to use for female voices
            male_voice_id: The voice ID to use for male voices
        
        Returns:
            voice_id (str)
        """
        if gender == 'female':
            return female_voice_id
        else:
            return male_voice_id


# ===========================
# Example Usage
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    
    # Test cases
    test_stories = [
        {
            "username": "throwaway_girl123",
            "subreddit": "relationship_advice",
            "story": "My boyfriend cheated on me...",
            "expected": "female"
        },
        {
            "username": "dude_who_knows",
            "subreddit": "TalesFromRetail",
            "story": "I was working at the store when a customer...",
            "expected": "male"
        },
        {
            "username": "random_user_456",
            "subreddit": "AITAH",
            "story": "I told my best friend the truth about her boyfriend...",
            "expected": "female"  # Because of "her boyfriend" reference
        },
        {
            "username": "anonymous_2023",
            "subreddit": "confession",
            "story": "I have been lying to my husband for years...",
            "expected": "female"  # Because of "my husband"
        },
        {
            "username": "xyz_123",
            "subreddit": "TrueOffMyChest",
            "story": "I just need to get this off my chest, my wife left me...",
            "expected": "male"  # Because of "my wife"
        }
    ]
    
    print("🎯 Gender Detection Tests:")
    print("=" * 60)
    
    for i, test in enumerate(test_stories, 1):
        result = detector.detect_gender(
            username=test.get("username"),
            subreddit=test.get("subreddit"),
            story_text=test.get("story")
        )
        
        status = "✅" if result == test["expected"] else "❌"
        print(f"{status} Test {i}:")
        print(f"   Username: {test.get('username')}")
        print(f"   Subreddit: {test.get('subreddit')}")
        print(f"   Detected: {result}")
        print(f"   Expected: {test['expected']}")
        print()import re

class GenderDetector:
    """
    Detect if a Reddit story is likely told by a male or female.
    Uses multiple signals: author username, subreddit, and story content.
    """
    
    # Keywords that indicate female narrator
    FEMALE_KEYWORDS = [
        'girl', 'woman', 'lady', 'miss', 'mrs', 'ms',
        'mom', 'mama', 'mother', 'aunt', 'sis', 'sister',
        'queen', 'princess', 'goddess',
        'she', 'her', 'hers', 'herself'
    ]
    
    # Keywords that indicate male narrator
    MALE_KEYWORDS = [
        'guy', 'dude', 'bro', 'man', 'mr', 'sir',
        'dad', 'father', 'uncle', 'bro', 'brother',
        'king', 'prince', 'god',
        'he', 'him', 'his', 'himself'
    ]
    
    # Subreddits with gender bias (manually curated)
    FEMALE_SUBREDDITS = [
        'TwoXChromosomes', 'AskWomen', 'Mommit', 'workingmoms',
        'woman', 'women', 'feminism'
    ]
    
    MALE_SUBREDDITS = [
        'AskMen', 'MensRights', 'daddit',
        'man', 'men', 'mgtow'
    ]
    
    def __init__(self, default_voice="male"):
        """
        Initialize the detector.
        
        Args:
            default_voice: Fallback gender if detection is uncertain ('male' or 'female')
        """
        self.default_voice = default_voice
        
    def _normalize_text(self, text):
        """Clean text for analysis."""
        if not text:
            return ""
        return text.lower()
    
    def _check_keywords(self, text, keywords):
        """Check if any keywords are in the text."""
        if not text:
            return False
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def detect_from_username(self, username):
        """
        Detect gender from username.
        Returns: 'male', 'female', or None
        """
        if not username:
            return None
        
        username_lower = username.lower()
        
        # Check female keywords in username
        if self._check_keywords(username_lower, self.FEMALE_KEYWORDS):
            return 'female'
        
        # Check male keywords in username
        if self._check_keywords(username_lower, self.MALE_KEYWORDS):
            return 'male'
        
        return None
    
    def detect_from_subreddit(self, subreddit):
        """
        Detect gender from subreddit name.
        Returns: 'male', 'female', or None
        """
        if not subreddit:
            return None
        
        subreddit_lower = subreddit.lower()
        
        for female_sub in self.FEMALE_SUBREDDITS:
            if female_sub.lower() in subreddit_lower:
                return 'female'
        
        for male_sub in self.MALE_SUBREDDITS:
            if male_sub.lower() in subreddit_lower:
                return 'male'
        
        return None
    
    def detect_from_story(self, story_text):
        """
        Detect gender from story content using first-person pronouns and context.
        Returns: 'male', 'female', or None
        """
        if not story_text:
            return None
        
        story_lower = story_text.lower()
        
        # Count first-person references
        female_refs = 0
        male_refs = 0
        
        # Check for "my [relationship]" patterns
        female_patterns = [
            r'my (girlfriend|wife|fiancée|sister|mom|mother|aunt|daughter)',
            r'my (best friend|friend) .* she',
            r'my (best friend|friend) .* her',
            r'i am a (girl|woman|lady|mom|mother)',
            r'i\'m a (girl|woman|lady|mom|mother)',
        ]
        
        male_patterns = [
            r'my (boyfriend|husband|fiancé|brother|dad|father|uncle|son)',
            r'my (best friend|friend) .* he',
            r'my (best friend|friend) .* him',
            r'i am a (guy|man|dad|father)',
            r'i\'m a (guy|man|dad|father)',
        ]
        
        for pattern in female_patterns:
            if re.search(pattern, story_lower):
                female_refs += 1
        
        for pattern in male_patterns:
            if re.search(pattern, story_lower):
                male_refs += 1
        
        # Determine gender based on references
        if female_refs > male_refs:
            return 'female'
        elif male_refs > female_refs:
            return 'male'
        else:
            return None
    
    def detect_gender(self, username=None, subreddit=None, story_text=None):
        """
        Detect gender using all available signals.
        Returns: 'male' or 'female' (with fallback to default)
        """
        # Priority order: username > subreddit > story content > default
        
        # 1. Check username
        if username:
            result = self.detect_from_username(username)
            if result:
                return result
        
        # 2. Check subreddit
        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                return result
        
        # 3. Check story content
        if story_text:
            result = self.detect_from_story(story_text)
            if result:
                return result
        
        # 4. Fallback to default
        return self.default_voice
    
    def get_voice_by_gender(self, gender, female_voice_id="Jessica", male_voice_id="Brian"):
        """
        Get the appropriate voice ID based on detected gender.
        
        Args:
            gender: 'male' or 'female'
            female_voice_id: The voice ID to use for female voices
            male_voice_id: The voice ID to use for male voices
        
        Returns:
            voice_id (str)
        """
        if gender == 'female':
            return female_voice_id
        else:
            return male_voice_id


# ===========================
# Example Usage
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    
    # Test cases
    test_stories = [
        {
            "username": "throwaway_girl123",
            "subreddit": "relationship_advice",
            "story": "My boyfriend cheated on me...",
            "expected": "female"
        },
        {
            "username": "dude_who_knows",
            "subreddit": "TalesFromRetail",
            "story": "I was working at the store when a customer...",
            "expected": "male"
        },
        {
            "username": "random_user_456",
            "subreddit": "AITAH",
            "story": "I told my best friend the truth about her boyfriend...",
            "expected": "female"  # Because of "her boyfriend" reference
        },
        {
            "username": "anonymous_2023",
            "subreddit": "confession",
            "story": "I have been lying to my husband for years...",
            "expected": "female"  # Because of "my husband"
        },
        {
            "username": "xyz_123",
            "subreddit": "TrueOffMyChest",
            "story": "I just need to get this off my chest, my wife left me...",
            "expected": "male"  # Because of "my wife"
        }
    ]
    
    print("🎯 Gender Detection Tests:")
    print("=" * 60)
    
    for i, test in enumerate(test_stories, 1):
        result = detector.detect_gender(
            username=test.get("username"),
            subreddit=test.get("subreddit"),
            story_text=test.get("story")
        )
        
        status = "✅" if result == test["expected"] else "❌"
        print(f"{status} Test {i}:")
        print(f"   Username: {test.get('username')}")
        print(f"   Subreddit: {test.get('subreddit')}")
        print(f"   Detected: {result}")
        print(f"   Expected: {test['expected']}")
        print()import re

class GenderDetector:
    """
    Detect if a Reddit story is likely told by a male or female.
    Uses multiple signals: author username, subreddit, and story content.
    """
    
    # Keywords that indicate female narrator
    FEMALE_KEYWORDS = [
        'girl', 'woman', 'lady', 'miss', 'mrs', 'ms',
        'mom', 'mama', 'mother', 'aunt', 'sis', 'sister',
        'queen', 'princess', 'goddess',
        'she', 'her', 'hers', 'herself'
    ]
    
    # Keywords that indicate male narrator
    MALE_KEYWORDS = [
        'guy', 'dude', 'bro', 'man', 'mr', 'sir',
        'dad', 'father', 'uncle', 'bro', 'brother',
        'king', 'prince', 'god',
        'he', 'him', 'his', 'himself'
    ]
    
    # Subreddits with gender bias (manually curated)
    FEMALE_SUBREDDITS = [
        'TwoXChromosomes', 'AskWomen', 'Mommit', 'workingmoms',
        'woman', 'women', 'feminism'
    ]
    
    MALE_SUBREDDITS = [
        'AskMen', 'MensRights', 'daddit',
        'man', 'men', 'mgtow'
    ]
    
    def __init__(self, default_voice="male"):
        """
        Initialize the detector.
        
        Args:
            default_voice: Fallback gender if detection is uncertain ('male' or 'female')
        """
        self.default_voice = default_voice
        
    def _normalize_text(self, text):
        """Clean text for analysis."""
        if not text:
            return ""
        return text.lower()
    
    def _check_keywords(self, text, keywords):
        """Check if any keywords are in the text."""
        if not text:
            return False
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def detect_from_username(self, username):
        """
        Detect gender from username.
        Returns: 'male', 'female', or None
        """
        if not username:
            return None
        
        username_lower = username.lower()
        
        # Check female keywords in username
        if self._check_keywords(username_lower, self.FEMALE_KEYWORDS):
            return 'female'
        
        # Check male keywords in username
        if self._check_keywords(username_lower, self.MALE_KEYWORDS):
            return 'male'
        
        return None
    
    def detect_from_subreddit(self, subreddit):
        """
        Detect gender from subreddit name.
        Returns: 'male', 'female', or None
        """
        if not subreddit:
            return None
        
        subreddit_lower = subreddit.lower()
        
        for female_sub in self.FEMALE_SUBREDDITS:
            if female_sub.lower() in subreddit_lower:
                return 'female'
        
        for male_sub in self.MALE_SUBREDDITS:
            if male_sub.lower() in subreddit_lower:
                return 'male'
        
        return None
    
    def detect_from_story(self, story_text):
        """
        Detect gender from story content using first-person pronouns and context.
        Returns: 'male', 'female', or None
        """
        if not story_text:
            return None
        
        story_lower = story_text.lower()
        
        # Count first-person references
        female_refs = 0
        male_refs = 0
        
        # Check for "my [relationship]" patterns
        female_patterns = [
            r'my (girlfriend|wife|fiancée|sister|mom|mother|aunt|daughter)',
            r'my (best friend|friend) .* she',
            r'my (best friend|friend) .* her',
            r'i am a (girl|woman|lady|mom|mother)',
            r'i\'m a (girl|woman|lady|mom|mother)',
        ]
        
        male_patterns = [
            r'my (boyfriend|husband|fiancé|brother|dad|father|uncle|son)',
            r'my (best friend|friend) .* he',
            r'my (best friend|friend) .* him',
            r'i am a (guy|man|dad|father)',
            r'i\'m a (guy|man|dad|father)',
        ]
        
        for pattern in female_patterns:
            if re.search(pattern, story_lower):
                female_refs += 1
        
        for pattern in male_patterns:
            if re.search(pattern, story_lower):
                male_refs += 1
        
        # Determine gender based on references
        if female_refs > male_refs:
            return 'female'
        elif male_refs > female_refs:
            return 'male'
        else:
            return None
    
    def detect_gender(self, username=None, subreddit=None, story_text=None):
        """
        Detect gender using all available signals.
        Returns: 'male' or 'female' (with fallback to default)
        """
        # Priority order: username > subreddit > story content > default
        
        # 1. Check username
        if username:
            result = self.detect_from_username(username)
            if result:
                return result
        
        # 2. Check subreddit
        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                return result
        
        # 3. Check story content
        if story_text:
            result = self.detect_from_story(story_text)
            if result:
                return result
        
        # 4. Fallback to default
        return self.default_voice
    
    def get_voice_by_gender(self, gender, female_voice_id="Jessica", male_voice_id="Brian"):
        """
        Get the appropriate voice ID based on detected gender.
        
        Args:
            gender: 'male' or 'female'
            female_voice_id: The voice ID to use for female voices
            male_voice_id: The voice ID to use for male voices
        
        Returns:
            voice_id (str)
        """
        if gender == 'female':
            return female_voice_id
        else:
            return male_voice_id


# ===========================
# Example Usage
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    
    # Test cases
    test_stories = [
        {
            "username": "throwaway_girl123",
            "subreddit": "relationship_advice",
            "story": "My boyfriend cheated on me...",
            "expected": "female"
        },
        {
            "username": "dude_who_knows",
            "subreddit": "TalesFromRetail",
            "story": "I was working at the store when a customer...",
            "expected": "male"
        },
        {
            "username": "random_user_456",
            "subreddit": "AITAH",
            "story": "I told my best friend the truth about her boyfriend...",
            "expected": "female"  # Because of "her boyfriend" reference
        },
        {
            "username": "anonymous_2023",
            "subreddit": "confession",
            "story": "I have been lying to my husband for years...",
            "expected": "female"  # Because of "my husband"
        },
        {
            "username": "xyz_123",
            "subreddit": "TrueOffMyChest",
            "story": "I just need to get this off my chest, my wife left me...",
            "expected": "male"  # Because of "my wife"
        }
    ]
    
    print("🎯 Gender Detection Tests:")
    print("=" * 60)
    
    for i, test in enumerate(test_stories, 1):
        result = detector.detect_gender(
            username=test.get("username"),
            subreddit=test.get("subreddit"),
            story_text=test.get("story")
        )
        
        status = "✅" if result == test["expected"] else "❌"
        print(f"{status} Test {i}:")
        print(f"   Username: {test.get('username')}")
        print(f"   Subreddit: {test.get('subreddit')}")
        print(f"   Detected: {result}")
        print(f"   Expected: {test['expected']}")
        print()import re

class GenderDetector:
    """
    Detect if a Reddit story is likely told by a male or female.
    Uses multiple signals: author username, subreddit, and story content.
    """
    
    # Keywords that indicate female narrator
    FEMALE_KEYWORDS = [
        'girl', 'woman', 'lady', 'miss', 'mrs', 'ms',
        'mom', 'mama', 'mother', 'aunt', 'sis', 'sister',
        'queen', 'princess', 'goddess',
        'she', 'her', 'hers', 'herself'
    ]
    
    # Keywords that indicate male narrator
    MALE_KEYWORDS = [
        'guy', 'dude', 'bro', 'man', 'mr', 'sir',
        'dad', 'father', 'uncle', 'bro', 'brother',
        'king', 'prince', 'god',
        'he', 'him', 'his', 'himself'
    ]
    
    # Subreddits with gender bias (manually curated)
    FEMALE_SUBREDDITS = [
        'TwoXChromosomes', 'AskWomen', 'Mommit', 'workingmoms',
        'woman', 'women', 'feminism'
    ]
    
    MALE_SUBREDDITS = [
        'AskMen', 'MensRights', 'daddit',
        'man', 'men', 'mgtow'
    ]
    
    def __init__(self, default_voice="male"):
        """
        Initialize the detector.
        
        Args:
            default_voice: Fallback gender if detection is uncertain ('male' or 'female')
        """
        self.default_voice = default_voice
        
    def _normalize_text(self, text):
        """Clean text for analysis."""
        if not text:
            return ""
        return text.lower()
    
    def _check_keywords(self, text, keywords):
        """Check if any keywords are in the text."""
        if not text:
            return False
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def detect_from_username(self, username):
        """
        Detect gender from username.
        Returns: 'male', 'female', or None
        """
        if not username:
            return None
        
        username_lower = username.lower()
        
        # Check female keywords in username
        if self._check_keywords(username_lower, self.FEMALE_KEYWORDS):
            return 'female'
        
        # Check male keywords in username
        if self._check_keywords(username_lower, self.MALE_KEYWORDS):
            return 'male'
        
        return None
    
    def detect_from_subreddit(self, subreddit):
        """
        Detect gender from subreddit name.
        Returns: 'male', 'female', or None
        """
        if not subreddit:
            return None
        
        subreddit_lower = subreddit.lower()
        
        for female_sub in self.FEMALE_SUBREDDITS:
            if female_sub.lower() in subreddit_lower:
                return 'female'
        
        for male_sub in self.MALE_SUBREDDITS:
            if male_sub.lower() in subreddit_lower:
                return 'male'
        
        return None
    
    def detect_from_story(self, story_text):
        """
        Detect gender from story content using first-person pronouns and context.
        Returns: 'male', 'female', or None
        """
        if not story_text:
            return None
        
        story_lower = story_text.lower()
        
        # Count first-person references
        female_refs = 0
        male_refs = 0
        
        # Check for "my [relationship]" patterns
        female_patterns = [
            r'my (girlfriend|wife|fiancée|sister|mom|mother|aunt|daughter)',
            r'my (best friend|friend) .* she',
            r'my (best friend|friend) .* her',
            r'i am a (girl|woman|lady|mom|mother)',
            r'i\'m a (girl|woman|lady|mom|mother)',
        ]
        
        male_patterns = [
            r'my (boyfriend|husband|fiancé|brother|dad|father|uncle|son)',
            r'my (best friend|friend) .* he',
            r'my (best friend|friend) .* him',
            r'i am a (guy|man|dad|father)',
            r'i\'m a (guy|man|dad|father)',
        ]
        
        for pattern in female_patterns:
            if re.search(pattern, story_lower):
                female_refs += 1
        
        for pattern in male_patterns:
            if re.search(pattern, story_lower):
                male_refs += 1
        
        # Determine gender based on references
        if female_refs > male_refs:
            return 'female'
        elif male_refs > female_refs:
            return 'male'
        else:
            return None
    
    def detect_gender(self, username=None, subreddit=None, story_text=None):
        """
        Detect gender using all available signals.
        Returns: 'male' or 'female' (with fallback to default)
        """
        # Priority order: username > subreddit > story content > default
        
        # 1. Check username
        if username:
            result = self.detect_from_username(username)
            if result:
                return result
        
        # 2. Check subreddit
        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                return result
        
        # 3. Check story content
        if story_text:
            result = self.detect_from_story(story_text)
            if result:
                return result
        
        # 4. Fallback to default
        return self.default_voice
    
    def get_voice_by_gender(self, gender, female_voice_id="Jessica", male_voice_id="Brian"):
        """
        Get the appropriate voice ID based on detected gender.
        
        Args:
            gender: 'male' or 'female'
            female_voice_id: The voice ID to use for female voices
            male_voice_id: The voice ID to use for male voices
        
        Returns:
            voice_id (str)
        """
        if gender == 'female':
            return female_voice_id
        else:
            return male_voice_id


# ===========================
# Example Usage
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    
    # Test cases
    test_stories = [
        {
            "username": "throwaway_girl123",
            "subreddit": "relationship_advice",
            "story": "My boyfriend cheated on me...",
            "expected": "female"
        },
        {
            "username": "dude_who_knows",
            "subreddit": "TalesFromRetail",
            "story": "I was working at the store when a customer...",
            "expected": "male"
        },
        {
            "username": "random_user_456",
            "subreddit": "AITAH",
            "story": "I told my best friend the truth about her boyfriend...",
            "expected": "female"  # Because of "her boyfriend" reference
        },
        {
            "username": "anonymous_2023",
            "subreddit": "confession",
            "story": "I have been lying to my husband for years...",
            "expected": "female"  # Because of "my husband"
        },
        {
            "username": "xyz_123",
            "subreddit": "TrueOffMyChest",
            "story": "I just need to get this off my chest, my wife left me...",
            "expected": "male"  # Because of "my wife"
        }
    ]
    
    print("🎯 Gender Detection Tests:")
    print("=" * 60)
    
    for i, test in enumerate(test_stories, 1):
        result = detector.detect_gender(
            username=test.get("username"),
            subreddit=test.get("subreddit"),
            story_text=test.get("story")
        )
        
        status = "✅" if result == test["expected"] else "❌"
        print(f"{status} Test {i}:")
        print(f"   Username: {test.get('username')}")
        print(f"   Subreddit: {test.get('subreddit')}")
        print(f"   Detected: {result}")
        print(f"   Expected: {test['expected']}")
        print()import re

class GenderDetector:
    """
    Detect if a Reddit story is likely told by a male or female.
    Uses multiple signals: author username, subreddit, and story content.
    """
    
    # Keywords that indicate female narrator
    FEMALE_KEYWORDS = [
        'girl', 'woman', 'lady', 'miss', 'mrs', 'ms',
        'mom', 'mama', 'mother', 'aunt', 'sis', 'sister',
        'queen', 'princess', 'goddess',
        'she', 'her', 'hers', 'herself'
    ]
    
    # Keywords that indicate male narrator
    MALE_KEYWORDS = [
        'guy', 'dude', 'bro', 'man', 'mr', 'sir',
        'dad', 'father', 'uncle', 'bro', 'brother',
        'king', 'prince', 'god',
        'he', 'him', 'his', 'himself'
    ]
    
    # Subreddits with gender bias (manually curated)
    FEMALE_SUBREDDITS = [
        'TwoXChromosomes', 'AskWomen', 'Mommit', 'workingmoms',
        'woman', 'women', 'feminism'
    ]
    
    MALE_SUBREDDITS = [
        'AskMen', 'MensRights', 'daddit',
        'man', 'men', 'mgtow'
    ]
    
    def __init__(self, default_voice="male"):
        """
        Initialize the detector.
        
        Args:
            default_voice: Fallback gender if detection is uncertain ('male' or 'female')
        """
        self.default_voice = default_voice
        
    def _normalize_text(self, text):
        """Clean text for analysis."""
        if not text:
            return ""
        return text.lower()
    
    def _check_keywords(self, text, keywords):
        """Check if any keywords are in the text."""
        if not text:
            return False
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def detect_from_username(self, username):
        """
        Detect gender from username.
        Returns: 'male', 'female', or None
        """
        if not username:
            return None
        
        username_lower = username.lower()
        
        # Check female keywords in username
        if self._check_keywords(username_lower, self.FEMALE_KEYWORDS):
            return 'female'
        
        # Check male keywords in username
        if self._check_keywords(username_lower, self.MALE_KEYWORDS):
            return 'male'
        
        return None
    
    def detect_from_subreddit(self, subreddit):
        """
        Detect gender from subreddit name.
        Returns: 'male', 'female', or None
        """
        if not subreddit:
            return None
        
        subreddit_lower = subreddit.lower()
        
        for female_sub in self.FEMALE_SUBREDDITS:
            if female_sub.lower() in subreddit_lower:
                return 'female'
        
        for male_sub in self.MALE_SUBREDDITS:
            if male_sub.lower() in subreddit_lower:
                return 'male'
        
        return None
    
    def detect_from_story(self, story_text):
        """
        Detect gender from story content using first-person pronouns and context.
        Returns: 'male', 'female', or None
        """
        if not story_text:
            return None
        
        story_lower = story_text.lower()
        
        # Count first-person references
        female_refs = 0
        male_refs = 0
        
        # Check for "my [relationship]" patterns
        female_patterns = [
            r'my (girlfriend|wife|fiancée|sister|mom|mother|aunt|daughter)',
            r'my (best friend|friend) .* she',
            r'my (best friend|friend) .* her',
            r'i am a (girl|woman|lady|mom|mother)',
            r'i\'m a (girl|woman|lady|mom|mother)',
        ]
        
        male_patterns = [
            r'my (boyfriend|husband|fiancé|brother|dad|father|uncle|son)',
            r'my (best friend|friend) .* he',
            r'my (best friend|friend) .* him',
            r'i am a (guy|man|dad|father)',
            r'i\'m a (guy|man|dad|father)',
        ]
        
        for pattern in female_patterns:
            if re.search(pattern, story_lower):
                female_refs += 1
        
        for pattern in male_patterns:
            if re.search(pattern, story_lower):
                male_refs += 1
        
        # Determine gender based on references
        if female_refs > male_refs:
            return 'female'
        elif male_refs > female_refs:
            return 'male'
        else:
            return None
    
    def detect_gender(self, username=None, subreddit=None, story_text=None):
        """
        Detect gender using all available signals.
        Returns: 'male' or 'female' (with fallback to default)
        """
        # Priority order: username > subreddit > story content > default
        
        # 1. Check username
        if username:
            result = self.detect_from_username(username)
            if result:
                return result
        
        # 2. Check subreddit
        if subreddit:
            result = self.detect_from_subreddit(subreddit)
            if result:
                return result
        
        # 3. Check story content
        if story_text:
            result = self.detect_from_story(story_text)
            if result:
                return result
        
        # 4. Fallback to default
        return self.default_voice
    
    def get_voice_by_gender(self, gender, female_voice_id="Jessica", male_voice_id="Brian"):
        """
        Get the appropriate voice ID based on detected gender.
        
        Args:
            gender: 'male' or 'female'
            female_voice_id: The voice ID to use for female voices
            male_voice_id: The voice ID to use for male voices
        
        Returns:
            voice_id (str)
        """
        if gender == 'female':
            return female_voice_id
        else:
            return male_voice_id


# ===========================
# Example Usage
# ===========================
if __name__ == "__main__":
    detector = GenderDetector(default_voice="male")
    
    # Test cases
    test_stories = [
        {
            "username": "throwaway_girl123",
            "subreddit": "relationship_advice",
            "story": "My boyfriend cheated on me...",
            "expected": "female"
        },
        {
            "username": "dude_who_knows",
            "subreddit": "TalesFromRetail",
            "story": "I was working at the store when a customer...",
            "expected": "male"
        },
        {
            "username": "random_user_456",
            "subreddit": "AITAH",
            "story": "I told my best friend the truth about her boyfriend...",
            "expected": "female"  # Because of "her boyfriend" reference
        },
        {
            "username": "anonymous_2023",
            "subreddit": "confession",
            "story": "I have been lying to my husband for years...",
            "expected": "female"  # Because of "my husband"
        },
        {
            "username": "xyz_123",
            "subreddit": "TrueOffMyChest",
            "story": "I just need to get this off my chest, my wife left me...",
            "expected": "male"  # Because of "my wife"
        }
    ]
    
    print("🎯 Gender Detection Tests:")
    print("=" * 60)
    
    for i, test in enumerate(test_stories, 1):
        result = detector.detect_gender(
            username=test.get("username"),
            subreddit=test.get("subreddit"),
            story_text=test.get("story")
        )
        
        status = "✅" if result == test["expected"] else "❌"
        print(f"{status} Test {i}:")
        print(f"   Username: {test.get('username')}")
        print(f"   Subreddit: {test.get('subreddit')}")
        print(f"   Detected: {result}")
        print(f"   Expected: {test['expected']}")
        print()

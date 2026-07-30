import os
import json
import random
import glob
from datetime import datetime

class RedditStoryLoader:
    """
    Load Reddit stories from the organized local data structure.
    Tracks used story IDs to prevent duplicates.
    """
    
    def __init__(self, data_path="../Get_stories/reddit_stories"):
        """
        Initialize the loader.
        
        Args:
            data_path: Path to the reddit_stories folder
        """
        self.data_path = data_path
        self.used_ids_path = "used_story_ids.json"
        self.used_ids = self._load_used_ids()
        
    def _load_used_ids(self):
        """Load the list of already used story IDs."""
        if os.path.exists(self.used_ids_path):
            with open(self.used_ids_path, 'r') as f:
                return json.load(f)
        return []
    
    def _save_used_ids(self):
        """Save the updated list of used story IDs."""
        with open(self.used_ids_path, 'w') as f:
            json.dump(self.used_ids, f, indent=2)
    
    def _find_latest_file(self, subreddit_path, pattern):
        """
        Find the most recent file matching a pattern in a subreddit folder.
        """
        full_pattern = os.path.join(subreddit_path, pattern)
        files = glob.glob(full_pattern)
        if not files:
            return None
        # Sort by modification time, get the newest
        return max(files, key=os.path.getmtime)
    
    def get_available_stories(self, subreddit=None):
        """
        Get all available stories from all subreddits or a specific one.
        Prioritizes narrator_script_*.json files (which have comments).
        """
        all_stories = []
        
        # If specific subreddit requested
        if subreddit:
            subreddit_path = os.path.join(self.data_path, subreddit)
            if not os.path.exists(subreddit_path):
                print(f"⚠️ Subreddit folder not found: {subreddit_path}")
                return []
            
            # PRIORITY 1: Try narrator_script_*.json (has comments)
            narrator_file = self._find_latest_file(subreddit_path, "narrator_script_*.json")
            if narrator_file:
                print(f"   📄 Loading narrator scripts with comments from: {os.path.basename(narrator_file)}")
                with open(narrator_file, 'r') as f:
                    stories = json.load(f)
                    # Add subreddit if not present
                    for story in stories:
                        if 'subreddit' not in story:
                            story['subreddit'] = subreddit
                    all_stories.extend(stories)
                    return all_stories
            
            # PRIORITY 2: Try stories_with_comments_*.json
            stories_with_comments = self._find_latest_file(subreddit_path, "stories_with_comments_*.json")
            if stories_with_comments:
                print(f"   📄 Loading stories with comments from: {os.path.basename(stories_with_comments)}")
                with open(stories_with_comments, 'r') as f:
                    stories = json.load(f)
                    for story in stories:
                        if 'subreddit' not in story:
                            story['subreddit'] = subreddit
                    all_stories.extend(stories)
                    return all_stories
            
            # PRIORITY 3: Fallback to stories_*.json (no comments)
            stories_file = self._find_latest_file(subreddit_path, "stories_*.json")
            if stories_file:
                print(f"   📄 Loading raw stories from: {os.path.basename(stories_file)}")
                with open(stories_file, 'r') as f:
                    stories = json.load(f)
                    for story in stories:
                        if 'subreddit' not in story:
                            story['subreddit'] = subreddit
                    all_stories.extend(stories)
                    return all_stories
            
            print(f"   ⚠️ No stories file found in {subreddit_path}")
            return []
        
        # Get stories from ALL subreddits
        if not os.path.exists(self.data_path):
            print(f"⚠️ Data path not found: {self.data_path}")
            return []
            
        subreddits = [d for d in os.listdir(self.data_path) 
                     if os.path.isdir(os.path.join(self.data_path, d))]
        
        if not subreddits:
            print(f"⚠️ No subreddit folders found in {self.data_path}")
            return []
        
        print(f"   📂 Found {len(subreddits)} subreddit folders")
        
        for sub in subreddits:
            sub_path = os.path.join(self.data_path, sub)
            stories_loaded = False
            
            # PRIORITY 1: narrator_script_*.json
            narrator_file = self._find_latest_file(sub_path, "narrator_script_*.json")
            if narrator_file:
                try:
                    with open(narrator_file, 'r') as f:
                        stories = json.load(f)
                        for story in stories:
                            if 'subreddit' not in story:
                                story['subreddit'] = sub
                        all_stories.extend(stories)
                        print(f"   ✅ Loaded {len(stories)} stories from r/{sub} (narrator script)")
                        stories_loaded = True
                except Exception as e:
                    print(f"   ⚠️ Error loading {narrator_file}: {e}")
            
            # PRIORITY 2: stories_with_comments_*.json
            if not stories_loaded:
                stories_with_comments = self._find_latest_file(sub_path, "stories_with_comments_*.json")
                if stories_with_comments:
                    try:
                        with open(stories_with_comments, 'r') as f:
                            stories = json.load(f)
                            for story in stories:
                                if 'subreddit' not in story:
                                    story['subreddit'] = sub
                            all_stories.extend(stories)
                            print(f"   ✅ Loaded {len(stories)} stories from r/{sub} (with comments)")
                            stories_loaded = True
                    except Exception as e:
                        print(f"   ⚠️ Error loading {stories_with_comments}: {e}")
            
            # PRIORITY 3: stories_*.json (fallback)
            if not stories_loaded:
                stories_file = self._find_latest_file(sub_path, "stories_*.json")
                if stories_file:
                    try:
                        with open(stories_file, 'r') as f:
                            stories = json.load(f)
                            for story in stories:
                                if 'subreddit' not in story:
                                    story['subreddit'] = sub
                            all_stories.extend(stories)
                            print(f"   ✅ Loaded {len(stories)} stories from r/{sub} (raw)")
                            stories_loaded = True
                    except Exception as e:
                        print(f"   ⚠️ Error loading {stories_file}: {e}")
            
            if not stories_loaded:
                print(f"   ⚠️ No stories file found in r/{sub}")
        
        return all_stories
    
    def get_unused_stories(self, subreddit=None, limit=10):
        """
        Get stories that haven't been used yet.
        Returns a list of story objects.
        """
        all_stories = self.get_available_stories(subreddit)
        
        if not all_stories:
            print(f"   ⚠️ No stories available!")
            return []
        
        # Filter out used stories
        unused = []
        for story in all_stories:
            story_id = story.get('id')
            if story_id and story_id not in self.used_ids:
                unused.append(story)
        
        print(f"   📊 Found {len(unused)} unused stories out of {len(all_stories)} total")
        
        # Shuffle and limit
        random.shuffle(unused)
        return unused[:limit]
    
    def get_random_story(self, subreddit=None):
        """
        Get a single random unused story.
        Returns a story object or None if no stories available.
        """
        unused = self.get_unused_stories(subreddit, limit=1)
        if unused:
            return unused[0]
        return None
    
    def get_all_unused_stories(self, subreddit=None):
        """
        Get ALL unused stories (no limit).
        Useful for batch processing.
        """
        all_stories = self.get_available_stories(subreddit)
        
        unused = []
        for story in all_stories:
            story_id = story.get('id')
            if story_id and story_id not in self.used_ids:
                unused.append(story)
        
        return unused
    
    def mark_story_used(self, story_id):
        """
        Mark a story as used to prevent duplicates.
        """
        if story_id and story_id not in self.used_ids:
            self.used_ids.append(story_id)
            self._save_used_ids()
            print(f"   ✅ Marked story {story_id} as used")
    
    def mark_stories_used(self, story_ids):
        """
        Mark multiple stories as used.
        """
        count = 0
        for story_id in story_ids:
            if story_id and story_id not in self.used_ids:
                self.used_ids.append(story_id)
                count += 1
        if count > 0:
            self._save_used_ids()
            print(f"   ✅ Marked {count} stories as used")
        return count
    
    def get_stats(self):
        """
        Get statistics about the available stories.
        """
        all_stories = self.get_available_stories()
        total = len(all_stories)
        used = len(self.used_ids)
        unused = total - used
        
        # Count by subreddit
        subreddit_counts = {}
        has_comments_count = 0
        has_comment_script_count = 0
        
        for story in all_stories:
            sub = story.get('subreddit', 'unknown')
            subreddit_counts[sub] = subreddit_counts.get(sub, 0) + 1
            
            if 'top_comments' in story and story['top_comments']:
                has_comments_count += 1
            if 'comment_script' in story and story['comment_script']:
                has_comment_script_count += 1
        
        return {
            'total_stories': total,
            'used_stories': used,
            'unused_stories': unused,
            'subreddit_counts': subreddit_counts,
            'stories_with_comments': has_comments_count,
            'stories_with_comment_script': has_comment_script_count
        }
    
    def reset_used_ids(self):
        """
        Reset the used IDs list (useful for testing).
        """
        self.used_ids = []
        self._save_used_ids()
        print(f"   ✅ Reset used story IDs")


# ===========================
# Standalone usage example
# ===========================
if __name__ == "__main__":
    loader = RedditStoryLoader("../Get_stories/reddit_stories")
    
    # Show stats
    stats = loader.get_stats()
    print("\n📊 STORY STATISTICS:")
    print(f"   Total stories: {stats['total_stories']}")
    print(f"   Used stories: {stats['used_stories']}")
    print(f"   Unused stories: {stats['unused_stories']}")
    print(f"   Stories with top_comments: {stats['stories_with_comments']}")
    print(f"   Stories with comment_script: {stats['stories_with_comment_script']}")
    print("\n   By subreddit:")
    for sub, count in stats['subreddit_counts'].items():
        print(f"      r/{sub}: {count} stories")
    
    # Get a random story
    print("\n🎲 Getting random story...")
    story = loader.get_random_story()
    if story:
        print(f"   📖 Title: {story.get('title', 'No title')}")
        print(f"   📂 Subreddit: {story.get('subreddit', 'Unknown')}")
        print(f"   🆔 ID: {story.get('id', 'No ID')}")
        print(f"   📝 Story length: {len(story.get('story', ''))} chars")
        
        # Check what data it has
        if 'comment_script' in story and story['comment_script']:
            print(f"   💬 Comment script: Yes ({len(story['comment_script'])} chars)")
        elif 'top_comments' in story and story['top_comments']:
            print(f"   💬 Top comments: {len(story['top_comments'])} comments")
        else:
            print(f"   💬 Comments: None")
    else:
        print("   ⚠️ No unused stories available!")
    
    # Show sample story data structure
    if story:
        print("\n📋 Sample story data structure:")
        for key in ['id', 'title', 'story', 'subreddit', 'score', 'author', 'comments', 'url']:
            if key in story:
                print(f"   {key}: {story[key]}")
        if 'top_comments' in story:
            print(f"   top_comments: {len(story['top_comments'])} entries")
        if 'comment_script' in story:
            print(f"   comment_script: {story['comment_script'][:100]}...")

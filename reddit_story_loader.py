import os
import json
import random
import glob
import shutil
from datetime import datetime
from config import DEBUG_MODE  # We'll add this to config.py

class RedditStoryLoader:
    """
    Load Reddit stories from the organized local data structure.
    Supports debug mode (testing) and production mode (real posts).
    """
    
    def __init__(self, data_path="../Get_stories/reddit_stories", debug_mode=True):
        """
        Initialize the loader.
        
        Args:
            data_path: Path to the reddit_stories folder
            debug_mode: If True, uses copies of stories and doesn't mark as used
        """
        self.data_path = data_path
        self.debug_mode = debug_mode
        
        # Use different used_ids files for debug vs production
        if debug_mode:
            self.used_ids_path = "test_used_ids.json"  # Separate tracking for testing
            print("🔬 DEBUG MODE ENABLED - Stories will NOT be marked as used")
        else:
            self.used_ids_path = "used_story_ids.json"  # Production tracking
            print("🚀 PRODUCTION MODE - Stories will be marked as used")
        
        self.used_ids = self._load_used_ids()
        
        # Create a debug copy of the data if in debug mode
        self.debug_data_path = None
        if debug_mode:
            self.debug_data_path = self._create_debug_copy()
    
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
    
    def _create_debug_copy(self):
        """
        Create a copy of the data for debugging/testing.
        This prevents accidental consumption of real stories.
        """
        # Create a debug folder if it doesn't exist
        debug_path = "reddit_stories_debug"
        
        # Only create the debug copy if it doesn't exist or if data_path has been updated
        if not os.path.exists(debug_path):
            print(f"🔬 Creating debug copy from {self.data_path}...")
            shutil.copytree(self.data_path, debug_path)
            print(f"   ✅ Debug copy created at: {debug_path}")
        else:
            print(f"🔬 Using existing debug copy at: {debug_path}")
        
        return debug_path
    
    def _get_active_data_path(self):
        """Return the active data path (debug or real)."""
        if self.debug_mode and self.debug_data_path:
            return self.debug_data_path
        return self.data_path
    
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
        active_path = self._get_active_data_path()
        
        # If specific subreddit requested
        if subreddit:
            subreddit_path = os.path.join(active_path, subreddit)
            if not os.path.exists(subreddit_path):
                print(f"⚠️ Subreddit folder not found: {subreddit_path}")
                return []
            
            # PRIORITY 1: narrator_script_*.json
            narrator_file = self._find_latest_file(subreddit_path, "narrator_script_*.json")
            if narrator_file:
                with open(narrator_file, 'r') as f:
                    stories = json.load(f)
                    for story in stories:
                        if 'subreddit' not in story:
                            story['subreddit'] = subreddit
                    all_stories.extend(stories)
                    return all_stories
            
            # PRIORITY 2: stories_with_comments_*.json
            stories_with_comments = self._find_latest_file(subreddit_path, "stories_with_comments_*.json")
            if stories_with_comments:
                with open(stories_with_comments, 'r') as f:
                    stories = json.load(f)
                    for story in stories:
                        if 'subreddit' not in story:
                            story['subreddit'] = subreddit
                    all_stories.extend(stories)
                    return all_stories
            
            # PRIORITY 3: stories_*.json (fallback)
            stories_file = self._find_latest_file(subreddit_path, "stories_*.json")
            if stories_file:
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
        if not os.path.exists(active_path):
            print(f"⚠️ Data path not found: {active_path}")
            return []
            
        subreddits = [d for d in os.listdir(active_path) 
                     if os.path.isdir(os.path.join(active_path, d))]
        
        if not subreddits:
            print(f"⚠️ No subreddit folders found in {active_path}")
            return []
        
        print(f"   📂 Found {len(subreddits)} subreddit folders")
        
        for sub in subreddits:
            sub_path = os.path.join(active_path, sub)
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
    
    def get_unused_stories(self, subreddit=None, limit=10, force_real=False):
        """
        Get stories that haven't been used yet.
        
        Args:
            subreddit: Optional specific subreddit
            limit: Max number of stories to return
            force_real: If True, use real data even in debug mode
        """
        # If forcing real data, temporarily switch to real path
        original_debug_mode = self.debug_mode
        if force_real:
            self.debug_mode = False
        
        all_stories = self.get_available_stories(subreddit)
        
        # Restore debug mode
        self.debug_mode = original_debug_mode
        
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
    
    def get_random_story(self, subreddit=None, force_real=False):
        """
        Get a single random unused story.
        
        Args:
            subreddit: Optional specific subreddit
            force_real: If True, use real data even in debug mode
        """
        unused = self.get_unused_stories(subreddit, limit=1, force_real=force_real)
        if unused:
            story = unused[0]
            if self.debug_mode:
                # In debug mode, add a [DEBUG] prefix to the title to clearly identify test videos
                if 'title' in story:
                    story['title'] = f"[TEST] {story['title']}"
            return story
        return None
    
    def mark_story_used(self, story_id):
        """
        Mark a story as used to prevent duplicates.
        In debug mode, this does nothing (stories are not consumed).
        
        Returns: True if marked, False if skipped (debug mode)
        """
        if self.debug_mode:
            print(f"   🔬 DEBUG MODE: Not marking story {story_id} as used (testing only)")
            return False
        
        if story_id and story_id not in self.used_ids:
            self.used_ids.append(story_id)
            self._save_used_ids()
            print(f"   ✅ Marked story {story_id} as used")
            return True
        return False
    
    def mark_stories_used(self, story_ids):
        """
        Mark multiple stories as used.
        In debug mode, this does nothing.
        """
        if self.debug_mode:
            print(f"   🔬 DEBUG MODE: Not marking {len(story_ids)} stories as used")
            return 0
        
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
        active_path = self._get_active_data_path()
        
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
            'stories_with_comment_script': has_comment_script_count,
            'data_path': active_path,
            'debug_mode': self.debug_mode
        }
    
    def reset_used_ids(self):
        """
        Reset the used IDs list.
        In debug mode, only affects test_used_ids.json.
        """
        self.used_ids = []
        self._save_used_ids()
        print(f"   ✅ Reset used story IDs ({'DEBUG' if self.debug_mode else 'PRODUCTION'})")
    
    def mark_story_unused(self, story_id):
        """
        Manually mark a story as unused (for recovery).
        """
        if story_id in self.used_ids:
            self.used_ids.remove(story_id)
            self._save_used_ids()
            print(f"   ✅ Marked story {story_id} as unused (recovered)")
            return True
        return False
    
    def get_test_stats(self):
        """
        Get stats specifically for the debug copy vs production.
        """
        debug_path = self.debug_data_path if self.debug_data_path else None
        production_path = self.data_path
        
        print("\n📊 DATA STATISTICS:")
        print(f"   Production data: {production_path}")
        print(f"   Debug data: {debug_path}")
        print(f"   Debug mode: {'ON' if self.debug_mode else 'OFF'}")
        print(f"   Used IDs file: {self.used_ids_path}")
        
        # Count stories in production
        prod_count = 0
        if os.path.exists(production_path):
            for sub in os.listdir(production_path):
                sub_path = os.path.join(production_path, sub)
                if os.path.isdir(sub_path):
                    files = glob.glob(os.path.join(sub_path, "narrator_script_*.json"))
                    if files:
                        with open(files[0], 'r') as f:
                            prod_count += len(json.load(f))
        
        # Count stories in debug
        debug_count = 0
        if debug_path and os.path.exists(debug_path):
            for sub in os.listdir(debug_path):
                sub_path = os.path.join(debug_path, sub)
                if os.path.isdir(sub_path):
                    files = glob.glob(os.path.join(sub_path, "narrator_script_*.json"))
                    if files:
                        with open(files[0], 'r') as f:
                            debug_count += len(json.load(f))
        
        return {
            'production_stories': prod_count,
            'debug_stories': debug_count,
            'used_ids': len(self.used_ids)
        }


# ===========================
# Standalone usage example
# ===========================
if __name__ == "__main__":
    print("=" * 60)
    print("🔬 TESTING IN DEBUG MODE (safe)")
    print("=" * 60)
    
    loader_debug = RedditStoryLoader("../Get_stories/reddit_stories", debug_mode=True)
    
    stats = loader_debug.get_stats()
    print(f"\n📊 Total stories: {stats['total_stories']}")
    print(f"   Used stories: {stats['used_stories']}")
    print(f"   Unused stories: {stats['unused_stories']}")
    print(f"   Debug mode: {stats['debug_mode']}")
    
    # Get a random story (will have [TEST] prefix)
    print("\n🎲 Getting random story (debug mode)...")
    story = loader_debug.get_random_story()
    if story:
        print(f"   Title: {story.get('title')}")
        print(f"   Subreddit: {story.get('subreddit')}")
        print(f"   ID: {story.get('id')}")
    else:
        print("   ⚠️ No stories available!")
    
    print("\n" + "=" * 60)
    print("🚀 PRODUCTION MODE (real posts)")
    print("=" * 60)
    
    loader_prod = RedditStoryLoader("../Get_stories/reddit_stories", debug_mode=False)
    
    stats = loader_prod.get_stats()
    print(f"\n📊 Total stories: {stats['total_stories']}")
    print(f"   Used stories: {stats['used_stories']}")
    print(f"   Unused stories: {stats['unused_stories']}")
    print(f"   Debug mode: {stats['debug_mode']}")

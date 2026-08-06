"""
process_comments.py
Place this inside each subreddit folder after copying comment JSONs.
It extracts top 5 comments and adds them to each story.
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime

# --- Subreddit-specific narrator transitions ---
SUBREDDIT_VIBES = {
    # --- Original subreddits ---
    "AITAH": {
        "intro": "Let's see what the Reddit community had to say...",
        "praise": "Thanks to everyone who shared their thoughts!",
        "comment_prefix": "u/"
    },
    "AmItheAsshole": {
        "intro": "The people of Reddit had some strong opinions on this one...",
        "praise": "A huge thank you to everyone who commented!",
        "comment_prefix": "u/"
    },
    "TrueOffMyChest": {
        "intro": "The community had some heartfelt responses...",
        "praise": "Thank you to everyone who shared their support!",
        "comment_prefix": "u/"
    },
    "tifu": {
        "intro": "Reddit had some hilarious reactions to this one...",
        "praise": "Thanks to everyone who chimed in!",
        "comment_prefix": "u/"
    },
    "relationship_advice": {
        "intro": "The relationship experts of Reddit weighed in...",
        "praise": "A big thank you to everyone who offered advice!",
        "comment_prefix": "u/"
    },
    "MaliciousCompliance": {
        "intro": "Reddit loved this satisfying story...",
        "praise": "Thanks to all the commenters for their input!",
        "comment_prefix": "u/"
    },
    "ProRevenge": {
        "intro": "The Reddit community was fully on board with this revenge...",
        "praise": "Thanks to everyone for their support!",
        "comment_prefix": "u/"
    },
    "pettyrevenge": {
        "intro": "Reddit enjoyed this petty revenge story...",
        "praise": "Thanks to the commenters for their thoughts!",
        "comment_prefix": "u/"
    },

    # --- NEW subreddits (your list) ---
    "TalesFromTheFrontDesk": {
        "intro": "The front desk workers of Reddit had some stories to tell...",
        "praise": "Thanks to all the hospitality workers who shared their experiences!",
        "comment_prefix": "u/"
    },
    "TalesFromRetail": {
        "intro": "Retail workers of Reddit shared their thoughts on this one...",
        "praise": "Thanks to everyone who's ever worked a retail job!",
        "comment_prefix": "u/"
    },
    "confession": {
        "intro": "Redditors had some honest confessions about this...",
        "praise": "Thanks to everyone who shared their truth!",
        "comment_prefix": "u/"
    },
    "self": {
        "intro": "The Reddit community shared their personal perspectives...",
        "praise": "Thanks to everyone who opened up about this!",
        "comment_prefix": "u/"
    },
    "offmychest": {
        "intro": "Redditors got things off their chest about this one...",
        "praise": "Thanks to everyone who shared their feelings!",
        "comment_prefix": "u/"
    },
    "unpopularopinion": {
        "intro": "Reddit had some spicy hot takes on this...",
        "praise": "Thanks to everyone who shared their (unpopular) opinions!",
        "comment_prefix": "u/"
    },
    "EntitledPeople": {
        "intro": "Reddit shared some wild entitled people stories...",
        "praise": "Thanks to everyone who shared their encounters with entitlement!",
        "comment_prefix": "u/"
    }
}


def load_stories():
    """Load all stories from JSON files in this folder."""
    stories = []
    for file in os.listdir('.'):
        if file.startswith('stories_') and file.endswith('.json') and not file.startswith('stories_with_comments'):
            try:
                with open(file, 'r', encoding='utf-8') as f:  # <-- FIX: added encoding='utf-8'
                    data = json.load(f)
                    if isinstance(data, list):
                        stories.extend(data)
                    else:
                        stories.append(data)
            except Exception as e:
                print(f"   ⚠️ Error loading {file}: {e}")
    return stories


def load_comment_json(story_id):
    """Load the comment JSON file for a specific story."""
    comment_file = f"comment_{story_id}.json"
    if os.path.exists(comment_file):
        try:
            with open(comment_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"   ⚠️ Error loading comment JSON: {e}")
    return None


def extract_top_comments(comment_data, limit=5):
    """
    Extract top comments from Reddit comment JSON.
    Returns list of {'author': '...', 'body': '...', 'score': '...'}
    """
    if not comment_data:
        return []

    comments = []

    try:
        # The comment data is usually in the second item
        # First item is the post itself, second item contains comments
        if len(comment_data) > 1:
            comment_listing = comment_data[1]
            for comment in comment_listing['data']['children']:
                comment_data = comment.get('data', {})
                author = comment_data.get('author', '[deleted]')
                body = comment_data.get('body', '')
                score = comment_data.get('score', 0)
                
                # Skip deleted comments, auto-moderator, and empty comments
                if (body and author != '[deleted]' and 
                    author != 'AutoModerator' and 
                    len(body) > 10):
                    comments.append({
                        'author': author,
                        'body': body.strip(),
                        'score': score
                    })
    except Exception as e:
        print(f"   ⚠️ Error parsing comments: {e}")

    # Sort by score (highest first)
    comments.sort(key=lambda x: x['score'], reverse=True)
    
    return comments[:limit]


def format_comments_for_narrator(comments, subreddit):
    """Format the top comments for the narrator's script."""
    if not comments:
        return "No comments were found for this story."

    vibe = SUBREDDIT_VIBES.get(subreddit, {
        "intro": "Here are the top comments from Reddit...",
        "praise": "Thanks to everyone who commented!",
        "comment_prefix": "u/"
    })

    lines = [vibe.get("intro", "Here are the top comments from Reddit...")]

    for i, comment in enumerate(comments, 1):
        author = comment.get('author', 'a Redditor')
        body = comment.get('body', '')
        
        # Clean up comment body (remove emojis, extra spaces)
        body = re.sub(r'[^\w\s.,!?\'"]', '', body)
        body = body[:200] + "..." if len(body) > 200 else body
        
        lines.append(f"Comment {i} from {vibe['comment_prefix']}{author}: '{body}'")

    lines.append(vibe.get("praise", "Thanks to everyone who shared their thoughts!"))
    
    return "\n\n".join(lines)


def process_all_stories():
    """Process all stories and add comments to them."""
    stories = load_stories()
    
    if not stories:
        print("❌ No stories found in this folder.")
        return

    print(f"📖 Found {len(stories)} stories.")
    
    updated_stories = []
    
    for story in stories:
        story_id = story.get('id', '')
        subreddit = story.get('subreddit', 'Unknown')
        title = story.get('title', 'Untitled')
        
        print(f"\n🔍 Processing: {title[:50]}...")
        
        # Load comment JSON
        comment_data = load_comment_json(story_id)
        
        if comment_data:
            top_comments = extract_top_comments(comment_data, limit=5)
            if top_comments:
                print(f"   ✅ Found {len(top_comments)} top comments.")
                comment_script = format_comments_for_narrator(top_comments, subreddit)
                story['top_comments'] = top_comments
                story['comment_script'] = comment_script
                story['has_comments'] = True
            else:
                print("   ⚠️ No valid comments found.")
                story['has_comments'] = False
        else:
            print(f"   ⚠️ No comment JSON found for {story_id}")
            story['has_comments'] = False
        
        updated_stories.append(story)

    # Save updated stories
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"stories_with_comments_{timestamp}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(updated_stories, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Saved updated stories to {output_file}")
    
    # Also save a version for the narrator
    save_narrator_version(updated_stories)


def save_narrator_version(stories):
    """Save a clean narrator version with the comment script."""
    narrator_data = []
    for story in stories:
        if story.get('has_comments', False):
            narrator_data.append({
                'title': story['title'],
                'story': story['story'],
                'comment_script': story.get('comment_script', ''),
                'subreddit': story.get('subreddit', 'Unknown')
            })
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    narrator_file = f"narrator_script_{timestamp}.json"
    with open(narrator_file, 'w', encoding='utf-8') as f:
        json.dump(narrator_data, f, indent=2, ensure_ascii=False)
    
    print(f"📝 Saved narrator version to {narrator_file}")


if __name__ == "__main__":
    print("=" * 60)
    print("💬 REDDIT COMMENT PROCESSOR")
    print("=" * 60)
    
    comment_files = [f for f in os.listdir('.') if f.startswith('comment_') and f.endswith('.json')]
    
    if not comment_files:
        print("⚠️ No comment JSON files found.")
        print("📋 First, run generate_comment_links.py and manually copy the comment JSONs.")
    else:
        print(f"📁 Found {len(comment_files)} comment JSON files.")
        process_all_stories()
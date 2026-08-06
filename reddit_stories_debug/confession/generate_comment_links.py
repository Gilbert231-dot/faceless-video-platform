"""
generate_comment_links.py
Place this inside each subreddit folder (e.g., reddit_stories/AITAH/)
Run it to generate comment links for all stories in that folder.
"""

import json
import os
from pathlib import Path

def generate_comment_links():
    """Generate comment JSON URLs for all stories in this folder."""
    stories = []
    comment_links = []

    # Load all story JSON files in this folder
    for file in os.listdir('.'):
        if file.startswith('stories_') and file.endswith('.json'):
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                stories.extend(data)

    if not stories:
        print("❌ No stories found in this folder.")
        return

    # Generate comment links
    for story in stories:
        story_id = story.get('id', '')
        subreddit = story.get('subreddit', '')
        if story_id and subreddit:
            comment_url = f"https://www.reddit.com/r/{subreddit}/comments/{story_id}/.json"
            comment_links.append({
                'story_id': story_id,
                'title': story['title'],
                'comment_url': comment_url,
                'subreddit': subreddit
            })

    # Save to file
    output_file = 'comment_links.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(comment_links, f, indent=2, ensure_ascii=False)

    print(f"✅ Generated {len(comment_links)} comment links -> {output_file}")
    
    # Print URLs for easy copying
    print("\n📋 Copy these URLs to your browser (one by one):")
    for link in comment_links:
        print(f"   {link['comment_url']}")

if __name__ == "__main__":
    generate_comment_links()
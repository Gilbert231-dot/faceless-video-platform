import praw
import re
from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
from script_gen import adapt_reddit_story

def fetch_reddit_story(subreddit="AmItheAsshole", post_type="hot"):
    """Fetch the top post from a subreddit."""
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        raise Exception("Reddit API credentials not set in .env file")
    
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT
    )
    subreddit_obj = reddit.subreddit(subreddit)
    
    try:
        if post_type == "hot":
            post = next(subreddit_obj.hot(limit=1))
        elif post_type == "top":
            post = next(subreddit_obj.top(limit=1))
        else:
            post = next(subreddit_obj.new(limit=1))
    except StopIteration:
        raise Exception(f"No posts found in r/{subreddit}")
    
    title = post.title
    story = post.selftext.strip()
    
    if len(story) < 100:
        try:
            post = next(subreddit_obj.top(limit=1))
            title = post.title
            story = post.selftext.strip()
        except:
            pass
    
    story = story.strip()
    if len(story) < 100:
        raise Exception(f"Story from r/{subreddit} is too short or empty")
    
    return title, story


def get_reddit_story_with_fallback(subreddits=None, post_type="hot"):
    """Try multiple subreddits until a good story is found."""
    if subreddits is None:
        subreddits = [
            "AmItheAsshole",
            "TrueOffMyChest",
            "tifu",
            "relationship_advice",
            "MaliciousCompliance",
            "ProRevenge",
            "AskReddit",
            "pettyrevenge"
        ]
    
    errors = []
    for subreddit in subreddits:
        try:
            print(f"   🔍 Trying r/{subreddit}...")
            title, story = fetch_reddit_story(subreddit, post_type)
            print(f"   ✅ Found story in r/{subreddit}: {title[:50]}...")
            return subreddit, title, story
        except Exception as e:
            print(f"   ⚠️ r/{subreddit} failed: {e}")
            errors.append(f"r/{subreddit}: {e}")
            continue
    
    raise Exception(f"All subreddits failed: {', '.join(errors)}")
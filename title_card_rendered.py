import os
from PIL import Image, ImageDraw, ImageFont

def create_reddit_title_card(
    subreddit=None,
    title=None,
    upvotes=None,
    comments=None,
    output_path="title_card_rendered.png",
    template_path=None
):
    """
    Minimal fallback - just create a simple intro card or skip entirely.
    """
    print("   ℹ️ Title card generation skipped (no template used)")
    return None

if __name__ == "__main__":
    create_reddit_title_card()

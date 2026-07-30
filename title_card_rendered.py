import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import textwrap

def create_reddit_title_card(
    subreddit="redditStory",
    title="Your Story Title Here",
    upvotes="999+",
    comments="999+",
    output_path="title_card_rendered.png",
    width=1080,
    height=1920
):
    """
    Create a Reddit-style title card matching the viral TikTok template.
    
    Based on reverse-engineered analysis:
    - White rounded card
    - Reddit-style header with avatar icon
    - Bold title text
    - Metadata badges at bottom
    """
    
    # Create a transparent canvas
    card = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    
    # ----- CARD DIMENSIONS (from analysis) -----
    card_x = 140
    card_y = 60
    card_width = 800
    card_height = 570
    card_radius = 24
    
    # ----- COLORS (from analysis) -----
    white = (255, 255, 255, 255)
    dark_text = (32, 32, 32)  # #202020
    gray_text = (160, 160, 160)  # #A0A0A0
    subreddit_color = (51, 51, 51)  # #333333
    border_color = (236, 236, 236)  # #ECECEC
    reddit_orange = (255, 69, 0)  # Reddit orange
    
    # ----- FONT SIZES (from analysis) -----
    subreddit_font_size = 20
    title_font_size = 42
    metadata_font_size = 18
    avatar_size = 34
    
    # Load fonts (fallback to default if not found)
    try:
        # Try to load system fonts first
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", title_font_size)
        subreddit_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", subreddit_font_size)
        metadata_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", metadata_font_size)
    except:
        # Fallback to default
        title_font = ImageFont.load_default()
        subreddit_font = ImageFont.load_default()
        metadata_font = ImageFont.load_default()
        print("⚠️ Using default fonts. Consider installing Inter or Liberation fonts for better quality.")
    
    # ----- 1. DRAW THE ROUNDED CARD -----
    # Create a mask for rounded corners
    mask = Image.new('L', (card_width, card_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (0, 0, card_width, card_height),
        radius=card_radius,
        fill=255
    )
    
    # Create card image
    card_img = Image.new('RGBA', (card_width, card_height), white)
    
    # Add subtle border (1px #ECECEC)
    border_img = Image.new('RGBA', (card_width, card_height), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_img)
    border_draw.rounded_rectangle(
        (0, 0, card_width - 1, card_height - 1),
        radius=card_radius,
        outline=border_color,
        width=1
    )
    
    # Apply mask to both
    card_img.putalpha(mask)
    border_img.putalpha(mask)
    
    # Paste onto main canvas at position
    card.paste(card_img, (card_x, card_y), card_img)
    card.paste(border_img, (card_x, card_y), border_img)
    
    # ----- 2. REDDIT AVATAR ICON (Orange circle with alien) -----
    # This is simplified - you can make it more detailed if needed
    avatar_x = card_x + 30
    avatar_y = card_y + 45
    
    # Draw orange circle
    draw.ellipse(
        (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
        fill=reddit_orange,
        outline=None
    )
    
    # Draw white alien face (simplified)
    alien_x = avatar_x + avatar_size // 2
    alien_y = avatar_y + avatar_size // 2
    # White circle for alien face
    draw.ellipse(
        (alien_x - 10, alien_y - 10, alien_x + 10, alien_y + 10),
        fill=(255, 255, 255, 200)
    )
    # Eyes (small dots)
    draw.ellipse((alien_x - 6, alien_y - 4, alien_x - 3, alien_y - 1), fill=(255, 69, 0))
    draw.ellipse((alien_x + 3, alien_y - 4, alien_x + 6, alien_y - 1), fill=(255, 69, 0))
    # Antenna (simple line)
    draw.line((alien_x - 2, alien_y - 12, alien_x - 4, alien_y - 18), fill=(255, 69, 0), width=2)
    draw.line((alien_x + 2, alien_y - 12, alien_x + 4, alien_y - 18), fill=(255, 69, 0), width=2)
    
    # ----- 3. SUBREDDIT NAME -----
    subreddit_x = card_x + 80
    subreddit_y = card_y + 50
    subreddit_text = f"{subreddit}"
    
    draw.text(
        (subreddit_x, subreddit_y),
        subreddit_text,
        font=subreddit_font,
        fill=subreddit_color
    )
    
    # ----- 4. VERIFICATION BADGE (Blue checkmark) -----
    badge_x = subreddit_x + subreddit_font.getlength(subreddit_text) + 10
    badge_y = subreddit_y + 2
    badge_size = 16
    
    # Blue circle
    draw.ellipse(
        (badge_x, badge_y, badge_x + badge_size, badge_y + badge_size),
        fill=(29, 161, 242)  # Twitter blue
    )
    # White checkmark
    check_x = badge_x + badge_size // 2
    check_y = badge_y + badge_size // 2
    draw.line(
        (check_x - 4, check_y, check_x - 1, check_y + 4),
        fill=(255, 255, 255),
        width=2
    )
    draw.line(
        (check_x - 1, check_y + 4, check_x + 5, check_y - 3),
        fill=(255, 255, 255),
        width=2
    )
    
    # ----- 5. TITLE (Wrapped to fit) -----
    title_x = card_x + 30
    title_y = card_y + 100
    
    # Wrap title to fit within card width (with padding)
    max_title_width = card_width - 60  # 30px padding on each side
    
    # Calculate how many characters fit per line
    # Approximate: 42px font, each char ~20px wide
    chars_per_line = max_title_width // 22  # Conservative estimate
    wrapped_lines = textwrap.wrap(title, width=chars_per_line)
    
    # Limit to 6 lines (from analysis)
    if len(wrapped_lines) > 6:
        wrapped_lines = wrapped_lines[:6]
    
    # Draw each line
    line_spacing = 1.15  # From analysis
    for i, line in enumerate(wrapped_lines):
        line_y = title_y + (i * int(title_font_size * line_spacing))
        draw.text(
            (title_x, line_y),
            line,
            font=title_font,
            fill=dark_text
        )
    
    # ----- 6. METADATA BADGES (Bottom of card) -----
    metadata_y = card_y + card_height - 40
    
    # Heart icon & count (left)
    heart_x = card_x + 30
    # Draw heart (simple)
    heart_color = gray_text
    heart_points = [
        (heart_x + 10, metadata_y + 2),
        (heart_x + 6, metadata_y - 2),
        (heart_x + 2, metadata_y + 2),
        (heart_x + 6, metadata_y + 8),
        (heart_x + 10, metadata_y + 2),
    ]
    draw.polygon(heart_points, outline=heart_color, width=1)
    
    # Heart count
    draw.text(
        (heart_x + 20, metadata_y - 2),
        f" {upvotes}",
        font=metadata_font,
        fill=gray_text
    )
    
    # Comment icon & count (right)
    comment_x = card_x + card_width - 100
    # Draw speech bubble (simplified)
    draw.ellipse(
        (comment_x, metadata_y - 4, comment_x + 18, metadata_y + 14),
        outline=gray_text,
        width=1
    )
    # Comment count
    draw.text(
        (comment_x + 22, metadata_y - 2),
        f" {comments}",
        font=metadata_font,
        fill=gray_text
    )
    
    # ----- 7. SAVE THE IMAGE -----
    card.save(output_path, "PNG", dpi=(300, 300))
    print(f"✅ Reddit title card saved: {output_path}")
    return output_path


# ----- EXAMPLE USAGE -----
if __name__ == "__main__":
    create_reddit_title_card(
        subreddit="redditStory",
        title="Before my wedding, I heard them through the wall planning to ruin everything",
        upvotes="999+",
        comments="4300",
        output_path="reddit_card_demo.png"
    )

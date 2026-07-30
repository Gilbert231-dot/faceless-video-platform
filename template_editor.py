"""
template_editor.py - Overlays text onto a pre-made Reddit title card template.
"""

import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import textwrap

def overlay_text_on_template(
    subreddit: str,
    title: str,
    username: str = "u/StoryLab",
    template_path: str = "assets/templates/reddit_title_card.png",
    output_path: str = "title_card_rendered.png"
):
    """
    Load a PNG template and overlay text onto it.
    Handles multi-line titles automatically.
    """
    
    # 1. Load the template
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")
    
    img = Image.open(template_path).convert('RGBA')
    draw = ImageDraw.Draw(img)
    
    # 2. Get image dimensions
    width, height = img.size
    
    # 3. Load fonts (with fallbacks)
    font_size = 42
    try:
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 36)
        font_regular = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 28)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", font_size)
    except:
        font_bold = ImageFont.load_default()
        font_regular = ImageFont.load_default()
        font_title = ImageFont.load_default()
    
    # 4. Define positions (adjust based on your template!)
    subreddit_x = 100
    subreddit_y = 170
    title_x = width // 2
    title_y = 250
    title_max_width = width - 200
    
    # ---- CRITICAL FIX: ERASE THE ENTIRE TEXT AREAS ----
    # Erase the ENTIRE top-left area (subreddit + username)
    draw.rectangle(
        [50, 100, 600, 220],
        fill=(248, 248, 248)
    )
    
    # Erase the ENTIRE center area (title)
    draw.rectangle(
        [200, 200, width - 200, 380],
        fill=(248, 248, 248)
    )
    # -------------------------------------------------
    
    # 5. Draw subreddit
    draw.text((subreddit_x, subreddit_y), f"r/{subreddit}", fill=(16, 16, 16), font=font_bold)
    
    # 6. Draw title (multi-line support)
    lines = wrap_text(title, font_title, title_max_width)
    line_count = len(lines.split('\n'))
    
    # If title is very long, reduce font size to fit
    if line_count > 3:
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 34)
        except:
            font_title = ImageFont.load_default()
        lines = wrap_text(title, font_title, title_max_width)
        line_count = len(lines.split('\n'))
    
    if line_count > 4:
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 28)
        except:
            font_title = ImageFont.load_default()
        lines = wrap_text(title, font_title, title_max_width)
        line_count = len(lines.split('\n'))
    
    # Center each line
    line_height = 50
    total_height = line_count * line_height
    start_y = title_y - (total_height // 2) + 25
    
    for i, line in enumerate(lines.split('\n')):
        bbox = draw.textbbox((0, 0), line, font=font_title)
        line_width = bbox[2] - bbox[0]
        line_x = (width - line_width) // 2
        line_y = start_y + (i * line_height)
        draw.text((line_x, line_y), line, fill=(0, 0, 0), font=font_title)
    
    # 7. Save
    img.save(output_path, 'PNG')
    print(f"✅ Rendered title card with r/{subreddit}: {title[:30]}...")
    return output_path


def wrap_text(text, font, max_width):
    """Wrap text to fit within max_width."""
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        test_line = ' '.join(current_line + [word])
        bbox = font.getbbox(test_line)
        if bbox[2] - bbox[0] <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
    
    if current_line:
        lines.append(' '.join(current_line))
    
    return '\n'.join(lines)


if __name__ == "__main__":
    # Test with a long title
    overlay_text_on_template(
        subreddit="MaliciousCompliance",
        title="IT told me I didn't understand the systems, so I stopped fixing them. The store learned what I actually did.",
        template_path="assets/templates/reddit_title_card.png",
        output_path="test_rendered_card.png"
    )

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
    
    Args:
        subreddit: The subreddit name (e.g., "MaliciousCompliance")
        title: The story title
        username: Display username (default: "u/StoryLab")
        template_path: Path to the PNG template
        output_path: Where to save the rendered image
    """
    
    # 1. Load the template
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")
    
    img = Image.open(template_path).convert('RGBA')
    draw = ImageDraw.Draw(img)
    
    # 2. Get image dimensions
    width, height = img.size
    
    # 3. Load fonts (with fallbacks)
    try:
        # Try to use Liberation fonts (available on Linux)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 36)
        font_regular = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 28)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 42)
    except:
        # Fallback to default
        font_bold = ImageFont.load_default()
        font_regular = ImageFont.load_default()
        font_title = ImageFont.load_default()
    
    # 4. Define text positions (adjust these based on your template!)
    # These values are relative to a 1080x560 card
    # You may need to tweak them based on your template
    
    # --- Position for subreddit ---
    # Based on ChatGPT's analysis: subreddit is below username
    subreddit_x = 100   # Left margin
    subreddit_y = 170   # Vertical position (adjust as needed)
    
    # --- Position for title ---
    title_x = width // 2  # Center horizontally
    title_y = 250         # Vertical position (adjust as needed)
    title_max_width = width - 200  # 100px padding on each side
    
    # --- Position for username ---
    # (Optional - if your template has a username placeholder)
    username_x = 100
    username_y = 120
    
    # 5. Draw the subreddit
    draw.text((subreddit_x, subreddit_y), f"r/{subreddit}", fill=(16, 16, 16), font=font_bold)
    
    # 6. Draw the title (wrapped and centered)
    wrapped_title = wrap_text(title, font_title, title_max_width)
    
    for i, line in enumerate(wrapped_title.split('\n')):
        bbox = draw.textbbox((0, 0), line, font=font_title)
        line_width = bbox[2] - bbox[0]
        line_x = (width - line_width) // 2
        line_y = title_y + (i * 50)
        draw.text((line_x, line_y), line, fill=(0, 0, 0), font=font_title)
    
    # 7. (Optional) Draw username
    # draw.text((username_x, username_y), username, fill=(16, 16, 16), font=font_regular)
    
    # 8. Save the rendered image
    img.save(output_path, 'PNG')
    print(f"✅ Rendered title card: {output_path}")
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
    # Test the function
    overlay_text_on_template(
        subreddit="MaliciousCompliance",
        title="IT told me I didn't understand the systems, so I stopped fixing them. The store learned what I actually did.",
        username="u/StoryLab",
        template_path="assets/templates/reddit_title_card.png",
        output_path="test_rendered_card.png"
    )

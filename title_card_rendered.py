import os
from PIL import Image, ImageDraw, ImageFont
import textwrap

def create_reddit_title_card(
    subreddit="StoryLab",
    title="Story Title Here",
    upvotes="999+",
    comments="4300",
    output_path="title_card_rendered.png",
    template_path="assets/templates/reddit_frame.png"  # Your template
):
    """
    Load the existing Reddit template and overlay text on it.
    """
    
    # Check if template exists
    if os.path.exists(template_path):
        print(f"   📄 Loading template from: {template_path}")
        
        # Load the template image
        template = Image.open(template_path).convert('RGBA')
        width, height = template.size
        
        # Create drawing context
        draw = ImageDraw.Draw(template)
        
        # ----- POSITION YOUR TEXT -----
        # These positions need to match where the placeholders are in your template
        # Adjust these values based on your actual template layout
        
        # Subreddit name (replace {Subreddit} placeholder)
        subreddit_x = 100
        subreddit_y = 150
        subreddit_font_size = 24
        
        # Story title (replace Story_title placeholder)
        title_x = 100
        title_y = 250
        title_font_size = 36
        
        # Upvotes (replace 199999 placeholder)
        upvotes_x = 100
        upvotes_y = 500
        metadata_font_size = 20
        
        # Comments (replace 4300 placeholder)
        comments_x = 300
        comments_y = 500
        
        # ----- LOAD FONTS -----
        try:
            # Try to load system fonts for better quality
            subreddit_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", subreddit_font_size)
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", title_font_size)
            metadata_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", metadata_font_size)
        except:
            # Fallback to default
            subreddit_font = ImageFont.load_default()
            title_font = ImageFont.load_default()
            metadata_font = ImageFont.load_default()
            print("   ⚠️ Using default fonts. Install Liberation fonts for better quality.")
        
        # ----- DRAW TEXT ONTO TEMPLATE -----
        # Subreddit
        draw.text((subreddit_x, subreddit_y), subreddit, font=subreddit_font, fill=(255, 255, 255))
        
        # Title (wrap if too long)
        max_width = width - 200
        chars_per_line = max_width // 20  # Approximate
        wrapped_lines = textwrap.wrap(title, width=chars_per_line)
        for i, line in enumerate(wrapped_lines[:3]):  # Max 3 lines
            line_y = title_y + (i * 45)
            draw.text((title_x, line_y), line, font=title_font, fill=(255, 255, 255))
        
        # Metadata
        draw.text((upvotes_x, upvotes_y), f"❤️ {upvotes}", font=metadata_font, fill=(200, 200, 200))
        draw.text((comments_x, comments_y), f"💬 {comments}", font=metadata_font, fill=(200, 200, 200))
        
        # Save the result
        template.save(output_path, "PNG")
        print(f"   ✅ Title card created from template: {output_path}")
        return output_path
    
    else:
        print(f"   ⚠️ Template not found at {template_path}")
        print(f"   🎨 Generating fallback card...")
        return generate_fallback_card(subreddit, title, upvotes, comments, output_path)


def generate_fallback_card(subreddit, title, upvotes, comments, output_path):
    """Fallback: generate a simple card if template not found"""
    from PIL import Image, ImageDraw, ImageFont
    
    # Create a simple dark background
    img = Image.new('RGB', (1080, 1920), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 40)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 24)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    
    # Draw text
    draw.text((100, 100), subreddit, fill=(255, 255, 255), font=small_font)
    draw.text((100, 200), title, fill=(255, 255, 255), font=font)
    draw.text((100, 500), f"❤️ {upvotes}", fill=(200, 200, 200), font=small_font)
    draw.text((300, 500), f"💬 {comments}", fill=(200, 200, 200), font=small_font)
    
    img.save(output_path)
    print(f"   ✅ Fallback card generated: {output_path}")
    return output_path


if __name__ == "__main__":
    create_reddit_title_card(
        subreddit="StoryLab",
        title="Before my wedding, I heard them planning to ruin everything",
        upvotes="999+",
        comments="4300",
        output_path="title_card_rendered.png",
        template_path="assets/templates/reddit_frame.png"
    )

import os
from PIL import Image

def create_reddit_title_card(
    subreddit="StoryLab",
    title="Story Title",
    upvotes="999+",
    comments="4300",
    output_path="title_card_rendered.png",
    template_path="assets/templates/reddit_frame.png"  # Your template image
):
    """
    Load the existing Reddit template and overlay text on it.
    """
    
    # Check if template exists
    if not os.path.exists(template_path):
        print(f"⚠️ Template not found at {template_path}. Generating fallback...")
        # Fallback to your original generation code
        return generate_fallback_card(subreddit, title, upvotes, comments, output_path)
    
    # Load the template image
    template = Image.open(template_path).convert('RGBA')
    
    # Get dimensions
    width, height = template.size
    
    # Create a drawing context
    draw = ImageDraw.Draw(template)
    
    # ----- POSITIONS (from your template) -----
    # You need to adjust these based on where the text placeholders are in your template
    # Since I can't see exactly where they are, I'll use reasonable defaults
    
    # Subreddit name position
    subreddit_x = 100
    subreddit_y = 150
    subreddit_font_size = 24
    
    # Title position
    title_x = 100
    title_y = 250
    title_font_size = 36
    
    # Metadata positions
    upvotes_x = 100
    upvotes_y = 500
    comments_x = 300
    comments_y = 500
    metadata_font_size = 20
    
    # Load fonts (fallback if not available)
    try:
        subreddit_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", subreddit_font_size)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", title_font_size)
        metadata_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", metadata_font_size)
    except:
        subreddit_font = ImageFont.load_default()
        title_font = ImageFont.load_default()
        metadata_font = ImageFont.load_default()
    
    # Draw text onto the template
    draw.text((subreddit_x, subreddit_y), subreddit, font=subreddit_font, fill=(255, 255, 255))
    draw.text((title_x, title_y), title, font=title_font, fill=(255, 255, 255))
    draw.text((upvotes_x, upvotes_y), f"❤️ {upvotes}", font=metadata_font, fill=(200, 200, 200))
    draw.text((comments_x, comments_y), f"💬 {comments}", font=metadata_font, fill=(200, 200, 200))
    
    # Save the result
    template.save(output_path, "PNG")
    print(f"✅ Title card created from template: {output_path}")
    return output_path


def generate_fallback_card(subreddit, title, upvotes, comments, output_path):
    """Fallback: generate a simple card if template not found"""
    # Create a simple image
    img = Image.new('RGB', (1080, 1920), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 40)
    except:
        font = ImageFont.load_default()
    
    draw.text((100, 100), subreddit, fill=(255, 255, 255), font=font)
    draw.text((100, 200), title, fill=(255, 255, 255), font=font)
    
    img.save(output_path)
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

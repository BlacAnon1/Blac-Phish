from PIL import Image, ImageDraw

def generate_ms_logo(output_path="ms_logo.png", size=200):
    # Create a new image with white background
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)

    # Define Microsoft blue color (RGB)
    ms_blue = (0, 120, 215)

    # Calculate square size and spacing (4 squares in a 2x2 grid)
    square_size = size // 3
    spacing = size // 10
    offset = (size - 2 * square_size - spacing) // 2

    # Draw four blue squares
    positions = [
        (offset, offset),  # Top-left
        (offset + square_size + spacing, offset),  # Top-right
        (offset, offset + square_size + spacing),  # Bottom-left
        (offset + square_size + spacing, offset + square_size + spacing)  # Bottom-right
    ]

    for pos in positions:
        draw.rectangle(
            [pos, (pos[0] + square_size, pos[1] + square_size)],
            fill=ms_blue
        )

    # Save the image
    image.save(output_path, "PNG")
    print(f"Generated {output_path} successfully")

if __name__ == "__main__":
    generate_ms_logo("ms_logo.png")

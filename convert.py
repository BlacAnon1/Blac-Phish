from PIL import Image
import os

def convert_to_favicon(input_png="ms_logo.png", output_ico="favicon.ico", favicon_size=(16, 16)):
    # Check if input file exists
    if not os.path.exists(input_png):
        print(f"Error: {input_png} not found in {os.getcwd()}")
        return

    try:
        # Open the PNG image
        image = Image.open(input_png)

        # Ensure image is in RGB mode (required for ICO)
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Resize to favicon size (16x16)
        image = image.resize(favicon_size, Image.LANCZOS)

        # Save as ICO
        image.save(output_ico, "ICO")
        print(f"Generated {output_ico} successfully from {input_png}")

    except Exception as e:
        print(f"Error converting {input_png} to {output_ico}: {e}")

if __name__ == "__main__":
    convert_to_favicon()

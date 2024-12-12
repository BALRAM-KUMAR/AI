from ultralytics import YOLO
import os

# Load the trained YOLO model
model = YOLO("/content/runs/detect/icon_detector2/weights/best.pt")  # Path to your trained model weights

# Directory containing images
images_path = "/content/sample_data/images/"  # Replace with your directory containing images

# Iterate over all images in the directory
for image_file in os.listdir(images_path):
    image_path = os.path.join(images_path, image_file)

    # Ensure it's an image file (optional check)
    if not image_file.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.gif')):
        continue

    # Run detection
    results = model.predict(source=image_path, save=False, conf=0.25)

    # Check if any detections are present
    if len(results[0].boxes) > 0:  # If detections exist
        print(f"Detection found in: {image_file}")
        print(results)
        break  # Stop the loop once detection is found
else:
    print("No icons detected in any of the images.")

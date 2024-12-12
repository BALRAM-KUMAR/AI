import os
import shutil
import cv2
import random

# Directories
image_dir = "/content/sample_data/images"  # Directory containing all images
dataset_dir = "/content/dataset"            # Root directory for the YOLO dataset
train_images_dir = os.path.join(dataset_dir, "images/train")
val_images_dir = os.path.join(dataset_dir, "images/val")
train_labels_dir = os.path.join(dataset_dir, "labels/train")
val_labels_dir = os.path.join(dataset_dir, "labels/val")
unknown_objects_dir = os.path.join(dataset_dir, "unknown_objects2")

# Create necessary directories
os.makedirs(train_images_dir, exist_ok=True)
os.makedirs(val_images_dir, exist_ok=True)
os.makedirs(train_labels_dir, exist_ok=True)
os.makedirs(val_labels_dir, exist_ok=True)
os.makedirs(unknown_objects_dir, exist_ok=True)

# Train-validation split ratio
split_ratio = 0.8  # 80% training, 20% validation

# Initialize class IDs for placeholders
class_counter = 0
class_map = {}

# Helper function to detect objects in an image using OpenCV
def detect_objects(image_path):
    # Load image
    image = cv2.imread(image_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Simple edge detection to identify potential objects
    edges = cv2.Canny(gray, 50, 150)

    # Find contours
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detected_objects = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w > 20 and h > 20:  # Filter out small objects
            detected_objects.append((x, y, w, h))

    return detected_objects

# Helper function to save cropped detected objects for manual identification
def save_cropped_objects(image_path, detected_objects, image_id):
    image = cv2.imread(image_path)
    cropped_paths = []

    for idx, (x, y, w, h) in enumerate(detected_objects):
        cropped = image[y:y+h, x:x+w]
        cropped_filename = f"{image_id}_object_{idx + 1}.png"
        cropped_path = os.path.join(unknown_objects_dir, cropped_filename)
        cv2.imwrite(cropped_path, cropped)
        cropped_paths.append(cropped_filename)

    return cropped_paths

# Helper function to process images
def process_images(files, images_dir, labels_dir):
    global class_counter

    for image_file in files:
        # Full path to the image
        image_path = os.path.join(image_dir, image_file)
        image_id = os.path.splitext(image_file)[0]

        # Detect objects in the image
        detected_objects = detect_objects(image_path)
        if not detected_objects:
            continue

        # Save cropped objects for manual labeling
        cropped_paths = save_cropped_objects(image_path, detected_objects, image_id)

        # Copy the image to the appropriate folder
        shutil.copy(image_path, images_dir)

        # Load the image to get dimensions
        image = cv2.imread(image_path)
        height, width, _ = image.shape

        # Create YOLO annotations for detected objects
        annotation_file = os.path.join(labels_dir, f"{image_id}.txt")
        with open(annotation_file, "w") as f:
            for idx, obj in enumerate(detected_objects):
                x, y, w, h = obj
                x_center = (x + w / 2) / width
                y_center = (y + h / 2) / height
                box_width = w / width
                box_height = h / height

                # Assign a numeric placeholder class ID
                class_id = class_counter
                class_map[f"{image_id}_object_{idx + 1}"] = class_id
                class_counter += 1

                f.write(f"{class_id} {x_center} {y_center} {box_width} {box_height}\n")

# Gather all images
image_files = [f for f in os.listdir(image_dir) if f.endswith((".png", ".jpg", ".jpeg"))]
random.shuffle(image_files)  # Shuffle for randomness

# Split into train and validation sets
split_index = int(len(image_files) * split_ratio)
train_files = image_files[:split_index]
val_files = image_files[split_index:]

# Process train and validation files
process_images(train_files, train_images_dir, train_labels_dir)
process_images(val_files, val_images_dir, val_labels_dir)

# Create dataset.yaml
yaml_file = os.path.join(dataset_dir, "dataset.yaml")
with open(yaml_file, "w") as f:
    f.write(f"path: {os.path.abspath(dataset_dir)}\n")
    f.write("train: images/train\n")
    f.write("val: images/val\n")
    f.write("\nnames:\n")
    for obj_id, class_id in class_map.items():
        f.write(f"  {class_id}: {obj_id}\n")

print(f"Dataset prepared successfully in '{dataset_dir}'!")
print(f"Cropped objects saved in '{unknown_objects_dir}' for manual identification!")
print(f"Dataset YAML file created: {yaml_file}")

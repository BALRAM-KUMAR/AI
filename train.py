from ultralytics import YOLO

# Path to your YOLOv8 configuration file
yaml_path = "/content/dataset/dataset.yaml"  # Replace with your dataset.yaml path

# Initialize the YOLOv8 model
model = YOLO("yolov8n.pt")  # Start with the 'nano' model (smallest)

# Train the model on CPU
model.train(
    data=yaml_path,      # Path to dataset YAML file
    epochs=50,           # Number of training epochs
    imgsz=640,           # Image size for training
    batch=16,            # Batch size
    name="icon_detector", # Save results in "runs/detect/icon_detector"
    device="cpu"         # Use CPU for training
)

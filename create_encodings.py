"""
create_encodings.py
-------------------
Builds encodings.pickle from a folder of face images.

Folder layout expected:

    dataset/
        baher/
            1.jpg
            2.jpg
        seif/
            1.jpg
        hamza/
            1.jpg

Each subfolder name becomes the person's label. Run this once (or whenever
you add new people / photos), then run main.py.

Usage:
    python3 create_encodings.py
"""

import os
import pickle
import face_recognition

DATASET_DIR = "dataset"
OUTPUT_FILE = "encodings.pickle"
VALID_EXT = (".jpg", ".jpeg", ".png")


def build_encodings():
    known_encodings = []
    known_names = []

    if not os.path.isdir(DATASET_DIR):
        print(f"[ERROR] '{DATASET_DIR}/' folder not found.")
        print("Create it and add one subfolder of images per person.")
        return

    people = sorted(
        d for d in os.listdir(DATASET_DIR)
        if os.path.isdir(os.path.join(DATASET_DIR, d))
    )

    if not people:
        print(f"[ERROR] No person folders found inside '{DATASET_DIR}/'.")
        return

    for name in people:
        person_dir = os.path.join(DATASET_DIR, name)
        images = [f for f in os.listdir(person_dir) if f.lower().endswith(VALID_EXT)]

        if not images:
            print(f"[WARN] No images for '{name}', skipping.")
            continue

        print(f"[INFO] Processing '{name}' ({len(images)} images)...")

        for img_name in images:
            path = os.path.join(person_dir, img_name)
            image = face_recognition.load_image_file(path)
            boxes = face_recognition.face_locations(image)
            encs = face_recognition.face_encodings(image, boxes)

            if len(encs) == 0:
                print(f"       [skip] no face found in {img_name}")
                continue

            # Use the first face found in each image
            known_encodings.append(encs[0])
            known_names.append(name.lower())

    if not known_encodings:
        print("[ERROR] No encodings were created. Check your images.")
        return

    data = {"encodings": known_encodings, "names": known_names}
    with open(OUTPUT_FILE, "wb") as f:
        f.write(pickle.dumps(data))

    print(f"[DONE] Saved {len(known_encodings)} encodings to {OUTPUT_FILE}")
    print(f"       People: {sorted(set(known_names))}")


if __name__ == "__main__":
    build_encodings()

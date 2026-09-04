"""
face_auth.py
=============
Phase: face-recognition security layer, using OpenCV's LBPH recognizer
(opencv-contrib-python) -- no dlib, no compilation risk on Python 3.14.

One-time setup:
    pip install opencv-contrib-python numpy
    python face_auth.py enroll "Arya"     # look at the webcam, follow prompts

This captures ~20 face samples, trains an LBPH model, and saves it to
face_model.yml + face_labels.json. Re-run enroll for additional authorized
people -- each gets added to the same model under their own label.

Runtime verification (called by app.py's /verify_face endpoint) takes a
single JPEG frame from the browser and checks it against the trained model.
"""

import json
import os
import sys
import urllib.request

import cv2
import numpy as np

MODEL_PATH = "face_model.yml"
LABELS_PATH = "face_labels.json"
SAMPLES_DIR = "face_data"

_CASCADE_LOCAL_PATH = "haarcascade_frontalface_default.xml"
_CASCADE_URL = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"


def _resolve_cascade_path() -> str:
    """
    Some opencv-contrib-python releases don't actually ship the Haar cascade
    data files at cv2.data.haarcascades (a packaging gap, not something you
    did wrong). Fall back to downloading a local copy once if that happens.
    """
    bundled_path = os.path.join(cv2.data.haarcascades, _CASCADE_LOCAL_PATH)
    if os.path.exists(bundled_path):
        return bundled_path

    if os.path.exists(_CASCADE_LOCAL_PATH):
        return _CASCADE_LOCAL_PATH

    print("Haar cascade file missing from your OpenCV install -- downloading a local copy once...")
    urllib.request.urlretrieve(_CASCADE_URL, _CASCADE_LOCAL_PATH)
    print(f"Saved to {_CASCADE_LOCAL_PATH}")
    return _CASCADE_LOCAL_PATH


_face_cascade = cv2.CascadeClassifier(_resolve_cascade_path())
if _face_cascade.empty():
    raise RuntimeError(
        "Could not load the face-detection cascade file even after fallback download. "
        "Check your internet connection, or manually download "
        f"{_CASCADE_URL} and save it as {_CASCADE_LOCAL_PATH} in this folder."
    )


def _detect_face_gray(frame_bgr):
    """Returns a cropped, resized grayscale face image, or None if no face found."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
    if len(faces) == 0:
        return None
    # Use the largest detected face (closest to camera) if multiple are found.
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    face = gray[y:y + h, x:x + w]
    return cv2.resize(face, (200, 200))


def enroll(name: str, num_samples: int = 20) -> None:
    """Capture face samples from the webcam and (re)train the recognizer. Run this manually, once per authorized person."""
    person_dir = os.path.join(SAMPLES_DIR, name)
    os.makedirs(person_dir, exist_ok=True)

    labels = _load_labels()
    if name not in labels.values():
        next_id = max(labels.keys(), default=-1) + 1
        labels[next_id] = name
    label_id = [k for k, v in labels.items() if v == name][0]

    cap = cv2.VideoCapture(0)
    print(f"Enrolling '{name}' -- look at the camera. Capturing {num_samples} samples...")
    count = 0
    while count < num_samples:
        ok, frame = cap.read()
        if not ok:
            continue
        face = _detect_face_gray(frame)
        if face is not None:
            cv2.imwrite(os.path.join(person_dir, f"{count}.jpg"), face)
            count += 1
            print(f"  captured {count}/{num_samples}")
        cv2.imshow("Enrolling -- press q to cancel", frame)
        if cv2.waitKey(200) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()

    _save_labels(labels)
    _train_model(labels)
    print(f"Done. '{name}' is now an authorized face (label {label_id}).")


def _load_labels() -> dict:
    if not os.path.exists(LABELS_PATH):
        return {}
    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


def _save_labels(labels: dict) -> None:
    with open(LABELS_PATH, "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2)


def _train_model(labels: dict) -> None:
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    images, ids = [], []
    for label_id, name in labels.items():
        person_dir = os.path.join(SAMPLES_DIR, name)
        if not os.path.isdir(person_dir):
            continue
        for fname in os.listdir(person_dir):
            img = cv2.imread(os.path.join(person_dir, fname), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                images.append(img)
                ids.append(label_id)
    if not images:
        print("No samples found -- nothing to train.")
        return
    recognizer.train(images, np.array(ids))
    recognizer.save(MODEL_PATH)


_recognizer = None
_labels_cache = None


def _load_model():
    global _recognizer, _labels_cache
    if _recognizer is None:
        if not os.path.exists(MODEL_PATH):
            return None, None
        _recognizer = cv2.face.LBPHFaceRecognizer_create()
        _recognizer.read(MODEL_PATH)
        _labels_cache = _load_labels()
    return _recognizer, _labels_cache


def verify_frame(frame_bgr, confidence_threshold: float = 100.0) -> tuple[bool, str]:
    """
    Check a single webcam frame against the trained model.
    LBPH confidence is a DISTANCE, not a percentage -- lower means a closer
    match. confidence_threshold=70 is a reasonable starting point; lower it
    for stricter matching, raise it if legitimate faces keep getting rejected.
    """
    recognizer, labels = _load_model()
    if recognizer is None:
        return False, "No enrolled faces yet -- run: python face_auth.py enroll \"YourName\""

    face = _detect_face_gray(frame_bgr)
    if face is None:
        return False, "No face detected in frame."

    label_id, confidence = recognizer.predict(face)
    name = labels.get(label_id, "unknown")

    if confidence <= confidence_threshold:
        return True, name
    return False, f"Face not recognized (closest match: {name}, confidence {confidence:.1f})."


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "enroll":
        enroll(sys.argv[2])
    else:
        print('Usage: python face_auth.py enroll "YourName"')
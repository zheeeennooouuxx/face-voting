import cv2
import os
import mysql.connector
from deepface import DeepFace
import time
import numpy as np
import datetime

import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# ======================
# CONFIG
# ======================
FACES_DIR = "faces"
MODEL_PATH = "hand_landmarker.task"

UNKNOWN_INTERVAL = 1
KNOWN_INTERVAL = 3

os.makedirs(FACES_DIR, exist_ok=True)

# ======================
# DATABASE
# ======================
def connect_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="face_voting"
    )

conn = connect_db()
c = conn.cursor()

# ======================
# HAND LANDMARKER
# ======================
BaseOptions = python.BaseOptions
HandLandmarker = vision.HandLandmarker
HandLandmarkerOptions = vision.HandLandmarkerOptions

options = HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=MODEL_PATH),
    num_hands=1
)

detector = HandLandmarker.create_from_options(options)

# ======================
# GESTURE
# ======================
def classify_gesture(lm_list):
    fingers = []

    if abs(lm_list[4][0] - lm_list[3][0]) > 20:
        fingers.append(1)
    else:
        fingers.append(0)

    tips = [8, 12, 16, 20]
    for tip in tips:
        if lm_list[tip][1] < lm_list[tip - 2][1]:
            fingers.append(1)
        else:
            fingers.append(0)

    thumb, index, middle, ring, pinky = fingers

    if index == 0 and middle == 0 and ring == 0 and pinky == 1:
        return "PINKY"
    elif thumb == 1 and index == 0:
        return "THUMB"
    elif index == 1 and middle == 0:
        return "ONE"
    elif index == 1 and middle == 1 and ring == 0:
        return "TWO"
    elif index == 1 and middle == 1 and ring == 1:
        return "THREE"
    elif index == 0 and middle == 0 and ring == 0 and pinky == 0:
        return "FIST"

    return None

# ======================
# FACE DETECTION
# ======================
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
)

def detect_face(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        return frame[y:y+h, x:x+w], (x, y, w, h)

    return None, None

# ======================
# FACE RECOGNITION
# ======================
def recognize_face(face_img):
    c.execute("SELECT id, nama, path_wajah, sudah_memilih FROM pemilih")
    users = c.fetchall()

    for user in users:
        id_user, nama, path, sudah = user
        try:
            result = DeepFace.verify(face_img, path, model_name='Facenet')
            if result["verified"]:
                return id_user, nama, sudah
        except:
            continue

    return None, None, None

# ======================
# REGISTER
# ======================
def register_user(face_img):
    if face_img is None:
        return

    nama = input("Masukkan nama: ")

    file_path = f"{FACES_DIR}/{nama}.jpg"
    cv2.imwrite(file_path, face_img)

    c.execute(
        "INSERT INTO pemilih (nama, path_wajah) VALUES (%s, %s)",
        (nama, file_path)
    )
    conn.commit()

# ======================
# STATE
# ======================
STATE_IDLE = "IDLE"
STATE_REGISTER = "REGISTER"
STATE_READY = "READY"
STATE_SELECT = "SELECT"
STATE_CONFIRM = "CONFIRM"

state = STATE_IDLE
temp_vote = None

gesture_buffer = []

def get_stable_gesture(current):
    global gesture_buffer

    if current is None:
        gesture_buffer.clear()
        return None

    gesture_buffer.append(current)
    if len(gesture_buffer) > 8:
        gesture_buffer.pop(0)

    if gesture_buffer.count(current) == len(gesture_buffer):
        return current

    return None

# ======================
# REALTIME CARD
# ======================
def draw_card(frame, nama, pilihan, status_text):
    h, w, _ = frame.shape
    x, y = w - 280, 50

    cv2.rectangle(frame, (x, y), (x+260, y+140), (255,255,255), -1)
    cv2.rectangle(frame, (x, y), (x+260, y+140), (0,0,0), 2)

    cv2.putText(frame, "VOTE CARD", (x+60, y+25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,0), 2)

    cv2.putText(frame, f"{nama}", (x+10, y+60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1)

    cv2.putText(frame, f"{pilihan}", (x+10, y+90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1)

    cv2.putText(frame, status_text, (x+10, y+120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,200,0), 2)

# ======================
# GUIDE (BARU)
# ======================
def draw_guide(frame, nama, sudah, state, temp_vote):
    if nama is None:
        text = "Mengenali Wajah...Naikkan jari kelingking untuk registrasi"
        color = (255,255,0)

    elif nama and sudah == 1:
        text = "SUDAH VOTING - AKSES DITOLAK"
        color = (0,0,255)

    else:
        if state == STATE_IDLE:
            text = "PINKY = Registrasi"
            color = (0,255,0)

        elif state == STATE_READY:
            text = "THUMB = Mulai Voting"
            color = (255,200,0)

        elif state == STATE_SELECT:
            text = "ONE / TWO / THREE untuk memilih"
            color = (200,255,0)

        elif state == STATE_CONFIRM:
            text = "PINKY = Konfirmasi | FIST = Batal"
            color = (0,200,255)

        else:
            text = ""
            color = (255,255,255)

    cv2.putText(frame, text, (30, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    if temp_vote is not None:
        cv2.putText(frame, f"Pilihan: {temp_vote}", (30, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)

# ======================
# MAIN
# ======================
def main():
    global state, temp_vote

    cap = cv2.VideoCapture(0)
    last_result = (None, None, None)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        result = detector.detect(mp_image)

        gesture_raw = None

        if result.hand_landmarks:
            h, w, _ = frame.shape
            lm_list = []
            for lm in result.hand_landmarks[0]:
                lm_list.append((int(lm.x*w), int(lm.y*h)))

            if len(lm_list) == 21:
                gesture_raw = classify_gesture(lm_list)

        gesture = get_stable_gesture(gesture_raw)

        face_img, face_box = detect_face(frame)

        if face_img is not None:
            face_small = cv2.resize(face_img, (224,224))
            last_result = recognize_face(face_small)

        id_user, nama, sudah = last_result

        # GUIDE ALWAYS ON
        draw_guide(frame, nama, sudah, state, temp_vote)

        # BLOCK
        if nama and sudah == 1:
            draw_card(frame, nama, "SUDAH VOTE", "LOCKED")
            cv2.imshow("Voting System", frame)
            continue

        # STATE
        if state == STATE_IDLE:
            if nama is None and gesture == "PINKY":
                state = STATE_REGISTER

        elif state == STATE_REGISTER:
            register_user(face_img)
            state = STATE_IDLE

        elif state == STATE_READY:
            if gesture == "THUMB":
                state = STATE_SELECT

        elif state == STATE_SELECT:
            if gesture == "ONE":
                temp_vote = 1
                state = STATE_CONFIRM
            elif gesture == "TWO":
                temp_vote = 2
                state = STATE_CONFIRM
            elif gesture == "THREE":
                temp_vote = 3
                state = STATE_CONFIRM

        elif state == STATE_CONFIRM:
            draw_card(frame, nama, f"Pilihan {temp_vote}", "KONFIRMASI")

            if gesture == "FIST":
                temp_vote = None
                state = STATE_SELECT

            elif gesture == "PINKY":
                if sudah == 1:
                    pass
                else:
                    kandidat = {1:"A",2:"B",3:"C"}
                    selected = kandidat[temp_vote]

                    c.execute("INSERT INTO suara (pemilih_id, pilihan) VALUES (%s,%s)",
                              (id_user, selected))
                    c.execute("UPDATE pemilih SET sudah_memilih=1 WHERE id=%s",
                              (id_user,))
                    conn.commit()

                    state = STATE_IDLE
                    temp_vote = None

        if nama and sudah == 0 and state == STATE_IDLE:
            state = STATE_READY

        if face_box:
            x,y,w,h = face_box
            cv2.rectangle(frame,(x,y),(x+w,y+h),(0,255,0),2)

        cv2.imshow("Voting System", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

main()
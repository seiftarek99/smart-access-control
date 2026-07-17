# Smart Access Control System

A face-recognition door entry system running on a Raspberry Pi 5, with a
physical button trigger, LED status feedback, and a PIN fallback for when a
face is not recognised.

Built as a **group project** during the STEAM Academy Programme
(Intelligent Systems) at the **University of Wolverhampton**, 19–29 January 2026.
The project received the **Best Group Project Award** for the programme.

---

## What it does

1. The system waits idle until the **button (GPIO 22)** is pressed — like a doorbell.
2. On press, the **camera** captures a frame and runs face recognition.
3. If an **authorized face** is matched, the **green LED** turns on for 5 seconds (access granted).
4. If not, the **red LED** turns on and the system asks for a **4-digit PIN**.
5. After **3 wrong PIN attempts**, the system enters a timed **lockout**.
6. A **long button press** toggles **silent mode** (LEDs off, for discreet use).
7. Every event is written to an **access log** (`access_log.csv`).

## Tech stack

- **Language:** Python 3
- **Libraries:** `face_recognition`, `opencv-python`, `numpy`, `gpiozero`, `picamera2`
- **Hardware:** Raspberry Pi 5, Pi Camera Module, green + red LEDs, push button, breadboard, resistors, jumper wires

## Wiring

| Component     | GPIO pin |
|---------------|----------|
| Green LED     | 17       |
| Red LED       | 27       |
| Push button   | 22       |

## Setup

```bash
# 1. Install dependencies (on the Raspberry Pi)
pip install -r requirements.txt

# 2. Add face images
#    dataset/<name>/<image>.jpg  (one folder per person)

# 3. Build the face encodings
python3 create_encodings.py

# 4. Run the system
python3 main.py
```

## Files

| File                  | Purpose                                            |
|-----------------------|----------------------------------------------------|
| `main.py`             | Main access-control loop                           |
| `create_encodings.py` | Builds `encodings.pickle` from the `dataset/` folder |
| `requirements.txt`    | Python dependencies                                |

---

## Team & roles

This was a collaborative project. Roles as they actually happened:

- **Hardware & integration:** Seif El-Din Tarek — breadboard wiring, camera and
  GPIO integration, getting the physical build to run reliably with the software.
- **Teammates:** Baher, Zeyad, and Hamza — software logic, documentation,
  testing, and presentation.

The code was created with AI assistance (ChatGPT and Claude), then tested and
adapted for the hardware — a normal part of how the team worked.

## Acknowledgements

Thanks to Prof. Ahmed Onsy and the University of Wolverhampton School of
Architecture, Computing and Engineering for the mentorship throughout the
programme.

## Security design

- PINs are **never stored in plaintext** — only SHA-256 hashes, compared in
  constant time (`hmac.compare_digest`) to resist timing attacks.
- Real deployments override the demo PINs via environment variables
  (`ACCESS_PIN_SHA256`, `EMERGENCY_PIN_SHA256`) so secrets never enter git.
- Wrong-PIN attempts are limited, with a timed lockout against brute force.
- Every event (grants, denials, lockouts, silent-mode toggles) is written to
  a timestamped CSV audit log.

## Notes / limitations

- Face recognition accuracy depends on lighting and the quality of the
  enrollment photos.
- The demo defaults (PIN 1234 / emergency 0000) are for testing only.

## License

MIT — see [LICENSE](LICENSE).

"""
Smart Access Control System
============================

A face-recognition door entry system for the Raspberry Pi 5.

Flow:
    1. System idles until the physical button (GPIO 22) is pressed.
    2. Short press -> camera captures a frame and runs face recognition.
    3. Authorized face  -> green LED, access granted.
    4. Unrecognized     -> red LED, 4-digit PIN fallback.
    5. Too many wrong PINs -> timed lockout.
    6. Long press -> toggles silent mode (LEDs stay off).

Security notes:
    - PINs are never stored in plaintext: only SHA-256 hashes live in the
      code, and comparisons are constant-time (hmac.compare_digest).
    - PIN hashes can be overridden via environment variables so real
      deployments never commit secrets:
          ACCESS_PIN_SHA256, EMERGENCY_PIN_SHA256
    - Every event is appended to a CSV audit log with timestamps.

Hardware:
    Raspberry Pi 5, Pi Camera Module,
    green LED (GPIO 17), red LED (GPIO 27), push button (GPIO 22).

Credits:
    Hardware integration, system design, and testing: Seif El-Din Tarek.
    Developed collaboratively with teammates Baher, Zeyad, and Hamza.
    Code created with AI assistance from ChatGPT and Claude.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import pickle
import sys
import time
from dataclasses import dataclass, field

import cv2
import face_recognition
import numpy as np
from gpiozero import LED, Button
from picamera2 import Picamera2


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    """Return the SHA-256 hex digest of a string."""
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    """All tunable settings in one place."""

    # GPIO pins
    green_pin: int = 17
    red_pin: int = 27
    button_pin: int = 22

    # PIN hashes (defaults are demo PINs 1234 / 0000).
    # Override in production:  export ACCESS_PIN_SHA256=<hash>
    pin_hash: str = field(
        default_factory=lambda: os.environ.get(
            "ACCESS_PIN_SHA256", _sha256("1234")
        )
    )
    emergency_pin_hash: str = field(
        default_factory=lambda: os.environ.get(
            "EMERGENCY_PIN_SHA256", _sha256("0000")
        )
    )

    # Security limits
    max_pin_tries: int = 3
    lockout_seconds: int = 15

    # Behaviour tuning
    cooldown_seconds: float = 3.0      # min gap between access attempts
    face_retries: int = 2              # face checks before PIN fallback
    long_press_seconds: float = 1.2    # hold time to toggle silent mode
    unlock_seconds: float = 5.0        # how long green stays on

    # Recognition tuning
    match_threshold: float = 0.5       # lower = stricter match
    frame_scale: float = 0.25          # downscale for speed

    # Access lists
    authorized: tuple[str, ...] = ("baher", "seif", "hamza")
    admins: tuple[str, ...] = ("baher",)

    # Files
    encodings_file: str = "encodings.pickle"
    log_file: str = "access_log.csv"


# ---------------------------------------------------------------------------
# Main system
# ---------------------------------------------------------------------------

class AccessControlSystem:
    """Owns the hardware, the recognition pipeline, and the access loop."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.silent_mode = False
        self.pin_tries = 0
        self.last_attempt = 0.0

        # Hardware
        self.green = LED(cfg.green_pin)
        self.red = LED(cfg.red_pin)
        self.button = Button(cfg.button_pin, pull_up=True)

        # Known faces
        with open(cfg.encodings_file, "rb") as f:
            data = pickle.load(f)
        self.known_encodings: list[np.ndarray] = data["encodings"]
        self.known_names: list[str] = data["names"]

        # Camera
        self.camera = Picamera2()
        self.camera.configure(
            self.camera.create_preview_configuration(
                main={"format": "XRGB8888", "size": (1280, 720)}
            )
        )
        self.camera.start()
        time.sleep(0.3)  # let the sensor settle

    # -- logging ------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def log(self, event: str, who: str = "", dist: str = "") -> None:
        """Append one event row to the CSV audit log."""
        new_file = not os.path.exists(self.cfg.log_file)
        with open(self.cfg.log_file, "a", encoding="utf-8") as f:
            if new_file:
                f.write("time,event,who,dist\n")
            f.write(f"{self._now()},{event},{who},{dist}\n")

    @staticmethod
    def say(msg: str) -> None:
        """Print a status line with a time prefix."""
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # -- LED helpers (respect silent mode) ----------------------------------

    def led_on(self, led: LED) -> None:
        if not self.silent_mode:
            led.on()

    def blink(self, led: LED, seconds: float,
              on_s: float = 0.12, off_s: float = 0.12) -> None:
        if self.silent_mode:
            return
        end = time.time() + seconds
        while time.time() < end:
            led.on()
            time.sleep(on_s)
            led.off()
            time.sleep(off_s)

    def grant(self) -> None:
        """Access granted: green LED for the configured unlock time."""
        self.red.off()
        self.led_on(self.green)
        time.sleep(self.cfg.unlock_seconds)
        self.green.off()
        self.pin_tries = 0

    def lockout(self) -> None:
        """Flash red and ignore input for the lockout period."""
        self.say(f"LOCKOUT {self.cfg.lockout_seconds}s")
        self.log("lockout")
        end = time.time() + self.cfg.lockout_seconds
        while time.time() < end:
            self.blink(self.red, 0.5, 0.08, 0.08)
            time.sleep(0.2)
        self.red.off()
        self.pin_tries = 0

    # -- face recognition ----------------------------------------------------

    def _capture_rgb(self) -> np.ndarray:
        """Capture a frame, downscale, and convert to RGB."""
        frame = self.camera.capture_array()
        small = cv2.resize(
            frame, (0, 0), fx=self.cfg.frame_scale, fy=self.cfg.frame_scale
        )
        return cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    def check_face_once(self) -> tuple[bool, str, float | None, str]:
        """
        Run one face check.

        Returns (ok, name, distance, reason) where reason is one of
        "authorized", "admin", "unknown", "no_face".
        """
        rgb = self._capture_rgb()
        locations = face_recognition.face_locations(rgb)
        encodings = face_recognition.face_encodings(rgb, locations)

        if not encodings:
            return False, "NoFace", None, "no_face"

        best_name, best_dist = "Unknown", None

        # If several faces are in view, keep only the closest match.
        for enc in encodings:
            dists = face_recognition.face_distance(self.known_encodings, enc)
            idx = int(np.argmin(dists))
            name, dist = self.known_names[idx], float(dists[idx])

            if best_dist is None or dist < best_dist:
                best_name, best_dist = name, dist

            if dist < self.cfg.match_threshold:
                if name in self.cfg.admins:
                    return True, name, dist, "admin"
                if name in self.cfg.authorized:
                    return True, name, dist, "authorized"

        return False, best_name, best_dist, "unknown"

    def check_face(self) -> tuple[bool, str, float | None, str]:
        """Retry the face check a few times before falling back to PIN."""
        ok, who, dist, reason = False, "Unknown", None, "unknown"
        for attempt in range(self.cfg.face_retries):
            ok, who, dist, reason = self.check_face_once()
            if ok:
                break
            self.say(f"Face retry {attempt + 1}/{self.cfg.face_retries}")
            self.blink(self.green, 0.2, 0.06, 0.06)
            time.sleep(0.2)
        return ok, who, dist, reason

    # -- input ---------------------------------------------------------------

    def wait_for_button(self) -> bool:
        """
        Block until the button is pressed.

        Returns True on a short press (proceed with access attempt).
        A long press toggles silent mode and returns False.
        """
        self.say("READY  Short press = check face | Long press = silent toggle")
        self.button.wait_for_press()
        t0 = time.time()
        self.button.wait_for_release()

        if time.time() - t0 >= self.cfg.long_press_seconds:
            self.silent_mode = not self.silent_mode
            state = "ON" if self.silent_mode else "OFF"
            self.say(f"SILENT MODE {state}")
            self.log("silent_toggle", state.lower())
            self.blink(self.red, 0.4, 0.08, 0.08)
            return False
        return True

    def _pin_matches(self, entered: str, stored_hash: str) -> bool:
        """Constant-time comparison of an entered PIN against a stored hash."""
        return hmac.compare_digest(_sha256(entered), stored_hash)

    def pin_fallback(self) -> None:
        """Ask for a PIN until success or lockout."""
        self.led_on(self.red)
        while True:
            try:
                pin = input("Enter 4-digit PIN: ").strip()
            except KeyboardInterrupt:
                raise SystemExit

            if self._pin_matches(pin, self.cfg.emergency_pin_hash):
                self.say("EMERGENCY PIN  Unlock")
                self.log("emergency_pin")
                self.grant()
                return

            if self._pin_matches(pin, self.cfg.pin_hash):
                self.say("PIN OK  Unlock")
                self.log("granted_pin")
                self.grant()
                return

            self.pin_tries += 1
            self.say(f"PIN BAD  tries={self.pin_tries}/{self.cfg.max_pin_tries}")
            self.log("bad_pin", "", str(self.pin_tries))
            self.blink(self.red, 1.2, 0.12, 0.12)
            self.led_on(self.red)

            if self.pin_tries >= self.cfg.max_pin_tries:
                self.red.off()
                self.lockout()
                return

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        self.say("SYSTEM START")
        self.say(f"Authorized: {list(self.cfg.authorized)}")
        self.say(f"Admin: {list(self.cfg.admins)}")
        self.log("start")

        while True:
            self.green.off()
            self.red.off()

            # Cooldown between attempts
            if time.time() - self.last_attempt < self.cfg.cooldown_seconds:
                time.sleep(0.05)
                continue

            if not self.wait_for_button():
                self.last_attempt = time.time()
                continue
            self.last_attempt = time.time()

            self.say("CAPTURE  Checking face")
            self.blink(self.green, 0.25, 0.06, 0.06)

            ok, who, dist, reason = self.check_face()

            if ok:
                self.say(f"GRANTED  {who}  dist={dist:.3f}")
                self.log("granted_face", who, f"{dist:.3f}")
                self.grant()
                continue

            if reason == "no_face":
                self.say("DENIED  No face detected")
                self.log("denied_no_face")
            elif dist is None:
                self.say(f"DENIED  best={who}")
                self.log("denied_unknown", who)
            else:
                self.say(f"DENIED  best={who}  dist={dist:.3f}")
                self.log("denied_face", who, f"{dist:.3f}")

            self.pin_fallback()

    def shutdown(self) -> None:
        """Release hardware cleanly."""
        self.green.off()
        self.red.off()
        self.camera.stop()
        self.log("shutdown")
        self.say("SYSTEM STOPPED")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    system = AccessControlSystem(Config())
    try:
        system.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        system.shutdown()


if __name__ == "__main__":
    main()

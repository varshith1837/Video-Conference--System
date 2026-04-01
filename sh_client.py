"""
Lan Conference Application Client

Password: In server Terminal.
"""

import socket
import threading
import json
import struct 
import time
import os
import io
import sys
import cv2
import numpy as np
from PIL import Image
import queue as Queue
import uuid

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve, QSize, QPoint, pyqtSlot, Q_ARG, QMetaObject
from PyQt5.QtGui import QPixmap, QImage, QFont, QIcon, QPalette, QColor, QPainter, QPen, QBrush
from PyQt5.QtWidgets import QAbstractItemView 

# Try to import mediapipe for gesture recognition
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except:
    MEDIAPIPE_AVAILABLE = False
    print("Warning: mediapipe not available. Gesture recognition disabled.")

try:
    import mss
    MSS_AVAILABLE = True
except:
    MSS_AVAILABLE = False

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except:
    PYAUDIO_AVAILABLE = False

# ====== Configuration ======
SERVER_TCP_PORT = 9000
SERVER_VIDEO_UDP_PORT = 10000
SERVER_AUDIO_UDP_PORT = 11000
SCREEN_TCP_PORT = 9001
FILE_TCP_PORT = 9002
LOCAL_VIDEO_LISTEN_PORT = 10001
LOCAL_AUDIO_LISTEN_PORT = 11001

VIDEO_WIDTH = 320
VIDEO_HEIGHT = 240
VIDEO_FPS = 20
VIDEO_CHUNK = 1100
JPEG_QUALITY = 80

AUDIO_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None
AUDIO_CHUNK = 256
AUDIO_INPUT_CHUNK = 256
MAX_UDP_SIZE = 65507

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 450
SCREEN_FPS = 10
SCREEN_QUALITY = 50

# ====== Color Themes ======
DARK_THEME = {
    "bg": "#0B0B0D",
    "panel": "#141416",
    "border": "#1E1E21",
    "primary": "#FF6B3D",
    "primary_hover": "#FF7E56",
    "leave": "#FF4040",
    "success": "#35E38A",
    "text_primary": "#F4F4F5",
    "text_secondary": "#C1C1C6",
    "text_tertiary": "#7D8087",
    "shadow": "rgba(0, 0, 0, 0.6)",
}

LIGHT_THEME = {
    "bg": "#C8CBCD",
    "panel": "#D6D8DA",
    "border": "#B3B6B8",
    "primary": "#FF6A3D",
    "primary_hover": "#FF7D56",
    "leave": "#FF4D4F",
    "success": "#2FC482",
    "text_primary": "#242627",
    "text_secondary": "#4B4F52",
    "text_tertiary": "#6F7376",
    "shadow": "rgba(0, 0, 0, 0.04)"
}

# ====== Utilities ======
def write_msg(sock, obj):
    try:
        if sock is None:
            return False
        data = json.dumps(obj).encode('utf-8')
        length = struct.pack('!I', len(data))
        sock.sendall(length + data)
        return True
    except Exception as e:
        print(f"write_msg error: {e}")
        return False

def read_msg(sock):
    try:
        if sock is None:
            return None
        sock.settimeout(15.0)
        length_data = b''
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        length = struct.unpack('!I', length_data)[0]
        if length > 50 * 1024 * 1024:
            return None
        data = b''
        while len(data) < length:
            chunk = sock.recv(min(16384, length - len(data)))
            if not chunk:
                return None
            data += chunk
        return json.loads(data.decode('utf-8'))
    except Exception as e:
        print(f"read_msg error: {e}")
        return None

def pack_control(obj):
    return (json.dumps(obj) + "\n").encode()

# ====== Global Sockets ======
tcp_sock = None
server_ip = None

video_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_recv_sock.bind(("0.0.0.0", LOCAL_VIDEO_LISTEN_PORT))

audio_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_recv_sock.bind(("0.0.0.0", LOCAL_AUDIO_LISTEN_PORT))

screen_share_sock = None
screen_view_sock = None

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "ConferenceFiles")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# ====== Whiteboard Canvas ======
class WhiteboardCanvas(QWidget):
    """Interactive whiteboard canvas with drawing capabilities"""
    
    draw_action = pyqtSignal(dict)
    cursor_moved = pyqtSignal(int, int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(800, 600)
        self.setMouseTracking(True)
        
        # Drawing state
        self.strokes = []
        self.shapes = []
        self.texts = []
        self.undo_stack = []
        
        # Current tool
        self.current_tool = "pen"
        self.pen_color = QColor("#000000")
        self.pen_width = 3
        
        # Drawing in progress
        self.drawing = False
        self.last_point = None
        self.current_stroke = []
        self.temp_shape = None
        
        # Remote cursors
        self.remote_cursors = {}
        
        self.setStyleSheet("background-color: white;")
    
    def set_tool(self, tool):
        self.current_tool = tool
    
    def set_color(self, color):
        self.pen_color = QColor(color)
    
    def set_width(self, width):
        self.pen_width = width
    
    def add_remote_stroke(self, stroke_data):
        """Add stroke from remote user"""
        self.strokes.append(stroke_data)
        self.update()
    
    def add_remote_shape(self, shape_data):
        """Add shape from remote user"""
        self.shapes.append(shape_data)
        self.update()
    
    def erase_element(self, element_id):
        """Erase element by ID"""
        self.strokes = [s for s in self.strokes if s.get("id") != element_id]
        self.shapes = [s for s in self.shapes if s.get("id") != element_id]
        self.update()
    
    def clear_canvas(self):
        """Clear entire canvas"""
        self.strokes = []
        self.shapes = []
        self.texts = []
        self.update()
    
    def undo(self):
        """Undo last action"""
        if self.strokes:
            self.undo_stack.append(("stroke", self.strokes.pop()))
        elif self.shapes:
            self.undo_stack.append(("shape", self.shapes.pop()))
        self.update()
    
    def update_remote_cursor(self, username, x, y, color):
        """Update remote user cursor position"""
        self.remote_cursors[username] = (x, y, color)
        self.update()
    
    def remove_remote_cursor(self, username):
        """Remove remote user cursor"""
        self.remote_cursors.pop(username, None)
        self.update()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = True
            self.last_point = event.pos()
            
            if self.current_tool == "pen":
                self.current_stroke = [{
                    "x": event.pos().x(),
                    "y": event.pos().y()
                }]
            elif self.current_tool in ["circle", "rect", "line"]:
                self.temp_shape = {
                    "start": event.pos(),
                    "end": event.pos()
                }
    
    def mouseMoveEvent(self, event):
        # Emit cursor position for remote users
        self.cursor_moved.emit(event.pos().x(), event.pos().y())
        
        if self.drawing and self.last_point:
            if self.current_tool == "pen":
                self.current_stroke.append({
                    "x": event.pos().x(),
                    "y": event.pos().y()
                })
                self.update()
            elif self.current_tool in ["circle", "rect", "line"]:
                self.temp_shape["end"] = event.pos()
                self.update()
            
            self.last_point = event.pos()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drawing:
            self.drawing = False
            
            if self.current_tool == "pen" and len(self.current_stroke) > 1:
                stroke_id = str(uuid.uuid4())
                stroke_data = {
                    "id": stroke_id,
                    "points": self.current_stroke,
                    "color": self.pen_color.name(),
                    "width": self.pen_width,
                    "timestamp": time.time()
                }
                self.strokes.append(stroke_data)
                self.draw_action.emit({"action": "draw", "data": stroke_data})
                self.current_stroke = []
            
            elif self.current_tool in ["circle", "rect", "line"] and self.temp_shape:
                shape_id = str(uuid.uuid4())
                shape_data = {
                    "id": shape_id,
                    "type": self.current_tool,
                    "start": {"x": self.temp_shape["start"].x(), "y": self.temp_shape["start"].y()},
                    "end": {"x": self.temp_shape["end"].x(), "y": self.temp_shape["end"].y()},
                    "color": self.pen_color.name(),
                    "width": self.pen_width,
                    "timestamp": time.time()
                }
                self.shapes.append(shape_data)
                self.draw_action.emit({"action": "shape", "data": shape_data})
                self.temp_shape = None
            
            self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw all strokes
        for stroke in self.strokes:
            pen = QPen(QColor(stroke["color"]), stroke["width"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            points = stroke["points"]
            for i in range(len(points) - 1):
                p1 = QPoint(points[i]["x"], points[i]["y"])
                p2 = QPoint(points[i + 1]["x"], points[i + 1]["y"])
                painter.drawLine(p1, p2)
        
        # Draw current stroke
        if self.drawing and self.current_tool == "pen" and len(self.current_stroke) > 1:
            pen = QPen(self.pen_color, self.pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            for i in range(len(self.current_stroke) - 1):
                p1 = QPoint(self.current_stroke[i]["x"], self.current_stroke[i]["y"])
                p2 = QPoint(self.current_stroke[i + 1]["x"], self.current_stroke[i + 1]["y"])
                painter.drawLine(p1, p2)
        
        # Draw all shapes
        for shape in self.shapes:
            pen = QPen(QColor(shape["color"]), shape["width"])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            start = QPoint(shape["start"]["x"], shape["start"]["y"])
            end = QPoint(shape["end"]["x"], shape["end"]["y"])
            
            if shape["type"] == "circle":
                radius = int(((end.x() - start.x()) ** 2 + (end.y() - start.y()) ** 2) ** 0.5)
                painter.drawEllipse(start, radius, radius)
            elif shape["type"] == "rect":
                painter.drawRect(start.x(), start.y(), end.x() - start.x(), end.y() - start.y())
            elif shape["type"] == "line":
                painter.drawLine(start, end)
        
        # Draw temp shape
        if self.temp_shape:
            pen = QPen(self.pen_color, self.pen_width)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            start = self.temp_shape["start"]
            end = self.temp_shape["end"]
            
            if self.current_tool == "circle":
                radius = int(((end.x() - start.x()) ** 2 + (end.y() - start.y()) ** 2) ** 0.5)
                painter.drawEllipse(start, radius, radius)
            elif self.current_tool == "rect":
                painter.drawRect(start.x(), start.y(), end.x() - start.x(), end.y() - start.y())
            elif self.current_tool == "line":
                painter.drawLine(start, end)
        
        # Draw remote cursors
        for username, (x, y, color) in self.remote_cursors.items():
            painter.setBrush(QBrush(QColor(color)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(x, y), 8, 8)
            painter.setPen(QColor(color))
            painter.setFont(QFont("Inter", 10, QFont.Bold))
            painter.drawText(x + 12, y + 5, username)

# ====== Gesture Floating Emoji ======
# ====== PART 1: Replace FloatingEmoji class (around line 367) ======
class FloatingEmoji(QLabel):
    """Floating emoji animation with better visibility"""
    
    def __init__(self, emoji, parent=None):
        super().__init__(emoji, parent)
        
        # Use a larger, more visible font
        self.setFont(QFont("Segoe UI Emoji", 72))  # Increased from 48
        self.setStyleSheet("""
            background: transparent;
            color: white;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        """)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAlignment(Qt.AlignCenter)
        
        # Size the label to fit the emoji
        self.adjustSize()
        
        # Random horizontal position
        import random
        parent_width = parent.width() if parent else 800
        start_x = random.randint(100, max(200, parent_width - 200))
        start_y = (parent.height() - 100) if parent else 500
        
        self.move(start_x, start_y)
        
        # Position animation - float upward
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setDuration(3000)
        self.animation.setStartValue(QPoint(start_x, start_y))
        self.animation.setEndValue(QPoint(start_x + random.randint(-100, 100), -150))
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.finished.connect(self.deleteLater)
        self.animation.start()
        
        # Opacity animation - fade out
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.opacity_animation.setDuration(3000)
        self.opacity_animation.setStartValue(1.0)
        self.opacity_animation.setEndValue(0.0)
        self.opacity_animation.setEasingCurve(QEasingCurve.InQuad)
        self.opacity_animation.start()
        
        # Raise to top
        self.raise_()


# ====== PART 2: Replace _show_gesture method (around line 1050) ======
def _show_gesture(self, from_user, gesture_type):
    """Show gesture with proper emoji display"""
    
    emoji_map = {
        "heart": "❤️",
        "thumbs_up": "👍",
        "peace": "✌️",
        "wave": "👋",
        "clap": "👏"
    }
    
    emoji = emoji_map.get(gesture_type)
    
    if not emoji:
        print(f"[DEBUG] Unknown gesture type: {gesture_type}")
        return
    
    # Log to chat
    self.log(f"{from_user} sent {emoji}", is_system=True)
    
    # Show THREE floating emojis for better visibility
    for i in range(3):
        floating = FloatingEmoji(emoji, self.video_container)
        floating.show()
        # Slight delay between each emoji
        QTimer.singleShot(i * 200, lambda f=floating: f.raise_())


# ====== PART 3: Improved detect_gesture method (around line 930) ======
def detect_gesture(self, frame):
    """Enhanced gesture detection with debug output"""
    if not self.gesture_enabled or not self.GESTURE_AVAILABLE:
        return None
    
    # Cooldown check
    if time.time() - self.gesture_cooldown < 2.0:
        return None
    
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = self.hands.process(frame_rgb)
    
    if not results.multi_hand_landmarks:
        return None
    
    num_hands = len(results.multi_hand_landmarks)
    print(f"[GESTURE] Detected {num_hands} hand(s)")
    
    # Two hands detected - check for heart or clap
    if num_hands >= 2:
        hand1 = results.multi_hand_landmarks[0]
        hand2 = results.multi_hand_landmarks[1]
        
        # Get key landmarks
        h1_thumb_tip = hand1.landmark[4]
        h1_index_tip = hand1.landmark[8]
        h2_thumb_tip = hand2.landmark[4]
        h2_index_tip = hand2.landmark[8]
        h1_palm = hand1.landmark[9]
        h2_palm = hand2.landmark[9]
        
        # Calculate distances
        thumb_distance = abs(h1_thumb_tip.x - h2_thumb_tip.x) + abs(h1_thumb_tip.y - h2_thumb_tip.y)
        index_distance = abs(h1_index_tip.x - h2_index_tip.x) + abs(h1_index_tip.y - h2_index_tip.y)
        palm_distance = abs(h1_palm.x - h2_palm.x) + abs(h1_palm.y - h2_palm.y)
        
        print(f"[GESTURE] Thumb dist: {thumb_distance:.2f}, Index dist: {index_distance:.2f}, Palm dist: {palm_distance:.2f}")
        
        # HEART: Thumbs and index fingers close together
        if thumb_distance < 0.2 and index_distance < 0.2:
            print("[GESTURE] HEART detected!")
            self.gesture_cooldown = time.time()
            return "heart"
        
        # CLAP: Palms close together with fingers extended
        if palm_distance < 0.25:
            # Check fingers extended on both hands
            h1_fingers_up = sum([
                hand1.landmark[8].y < hand1.landmark[6].y,
                hand1.landmark[12].y < hand1.landmark[10].y,
                hand1.landmark[16].y < hand1.landmark[14].y,
                hand1.landmark[20].y < hand1.landmark[18].y
            ])
            
            h2_fingers_up = sum([
                hand2.landmark[8].y < hand2.landmark[6].y,
                hand2.landmark[12].y < hand2.landmark[10].y,
                hand2.landmark[16].y < hand2.landmark[14].y,
                hand2.landmark[20].y < hand2.landmark[18].y
            ])
            
            print(f"[GESTURE] Fingers up: H1={h1_fingers_up}, H2={h2_fingers_up}")
            
            if h1_fingers_up >= 3 and h2_fingers_up >= 3:
                print("[GESTURE] CLAP detected!")
                self.gesture_cooldown = time.time()
                return "clap"
    
    # Single hand gestures
    for hand_landmarks in results.multi_hand_landmarks:
        thumb_tip = hand_landmarks.landmark[4]
        thumb_ip = hand_landmarks.landmark[3]
        index_tip = hand_landmarks.landmark[8]
        index_pip = hand_landmarks.landmark[6]
        middle_tip = hand_landmarks.landmark[12]
        middle_pip = hand_landmarks.landmark[10]
        ring_tip = hand_landmarks.landmark[16]
        ring_pip = hand_landmarks.landmark[14]
        pinky_tip = hand_landmarks.landmark[20]
        pinky_pip = hand_landmarks.landmark[18]
        wrist = hand_landmarks.landmark[0]
        
        # THUMBS UP
        thumb_up = thumb_tip.y < thumb_ip.y < wrist.y
        fingers_down = (
            index_tip.y > index_pip.y and
            middle_tip.y > middle_pip.y and
            ring_tip.y > ring_pip.y and
            pinky_tip.y > pinky_pip.y
        )
        
        if thumb_up and fingers_down:
            print("[GESTURE] THUMBS UP detected!")
            self.gesture_cooldown = time.time()
            return "thumbs_up"
        
        # PEACE SIGN
        index_up = index_tip.y < index_pip.y
        middle_up = middle_tip.y < middle_pip.y
        ring_down = ring_tip.y > ring_pip.y
        pinky_down = pinky_tip.y > pinky_pip.y
        finger_separation = abs(index_tip.x - middle_tip.x)
        
        if index_up and middle_up and ring_down and pinky_down and finger_separation > 0.03:
            print("[GESTURE] PEACE detected!")
            self.gesture_cooldown = time.time()
            return "peace"
        
        # WAVE
        fingers_up = sum([
            index_tip.y < index_pip.y,
            middle_tip.y < middle_pip.y,
            ring_tip.y < ring_pip.y,
            pinky_tip.y < pinky_pip.y
        ])
        
        if fingers_up >= 4:
            print("[GESTURE] WAVE detected!")
            self.gesture_cooldown = time.time()
            return "wave"
    
    return None

# ====== Conference Client ======
class ConferenceClient(QMainWindow):
    log_signal = pyqtSignal(str, bool, bool)
    update_users_signal = pyqtSignal()
    update_video_signal = pyqtSignal()
    update_screen_signal = pyqtSignal(object)
    gesture_signal = pyqtSignal(str, str)
    whiteboard_signal = pyqtSignal(dict)
    cursor_signal = pyqtSignal(str, int, int, str)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lan Conference Application")
        self.setGeometry(100, 100, 1600, 900)
        
        # State
        self.dark_mode = True
        self.theme = DARK_THEME.copy()
        self.username = None
        self.connected = False
        self.sending_video = False
        self.sending_audio = False
        self.sharing_screen = False
        self.viewing_screen = False
        self.screen_share_lock = threading.Lock()
        
        self.frames_by_src = {}
        self.active_video_sources = {}
        self.video_timeout = 2.0
        self.active_users = []
        self.selected_chat_user = None
        self.sidebar_visible = True
        self.screen_expanded = False
        self.auth_failed = False
        self.whiteboard_visible = False  # Track whiteboard visibility
        
        # Gesture recognition
        self.gesture_enabled = False
        if MEDIAPIPE_AVAILABLE:
            try:
                self.mp_hands = mp.solutions.hands
                self.hands = self.mp_hands.Hands(
                    static_image_mode=False,
                    max_num_hands=2,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )
                self.gesture_cooldown = 0
                self.GESTURE_AVAILABLE = True
            except:
                self.GESTURE_AVAILABLE = False
        else:
            self.GESTURE_AVAILABLE = False
        
        self.local_ip = self._get_local_ip()
        
        self.pa = pyaudio.PyAudio() if PYAUDIO_AVAILABLE else None
        self.audio_play_stream = None
        self.audio_capture_stream = None
        self.audio_buffer = Queue.Queue(maxsize=20)
        
        self.running = True
        self.video_cap = None
        
        # Start TCP receiver thread FIRST (before UI)
        self.tcp_thread = threading.Thread(target=self.tcp_receiver_loop, daemon=True)
        self.tcp_thread.start()
        time.sleep(0.1)  # Give thread time to start
        
        self._build_ui()
        self._apply_theme()
        
        self.log_signal.connect(self._handle_log)
        self.update_users_signal.connect(self._update_users_display)
        self.update_video_signal.connect(self._redraw_video)
        self.update_screen_signal.connect(self._update_screen_display)
        self.gesture_signal.connect(self._show_gesture)
        self.whiteboard_signal.connect(self._handle_whiteboard_action)
        self.cursor_signal.connect(self._update_remote_cursor)
        
        # Start other background threads
        threading.Thread(target=self.video_receiver_loop, daemon=True).start()
        threading.Thread(target=self.audio_receiver_loop, daemon=True).start()
        threading.Thread(target=self.audio_playback_loop, daemon=True).start()
        threading.Thread(target=self.video_cleanup_loop, daemon=True).start()
        
        self.video_timer = QTimer()
        self.video_timer.timeout.connect(self._redraw_video)
        self.video_timer.start(66)
        
        # self.cursor_timer = QTimer()
        # self.cursor_timer.timeout.connect(self._send_cursor_position)
        # self.cursor_timer.start(50)
    
    from PyQt5.QtCore import pyqtSlot, Q_ARG

    @pyqtSlot(str, int, str)
    def _add_file_card_slot(self, filename, size, from_user):
        """Qt slot to add file card on main thread"""
        print(f"[FILE_DEBUG] _add_file_card_slot called: {filename} from {from_user}")
        self._add_file_card(filename, size, from_user)

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
    
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        self._create_header(main_layout)
        
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)
        
        self._create_video_area(content_layout)
        self._create_sidebar(content_layout)
        
        main_layout.addWidget(content_widget, 1)
        self._create_bottom_controls(main_layout)
    
    def _create_header(self, parent_layout):
        self.header = QFrame()
        self.header.setFixedHeight(70)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(20, 15, 20, 15)
        
        left_widget = QWidget()
        left_layout = QHBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(15)
        
        app_label = QLabel("Lan Conference App 🐧")
        app_label.setFont(QFont("Inter", 20, QFont.Bold))
        left_layout.addWidget(app_label)
        
        self.status_frame = QFrame()
        self.status_frame.setFixedHeight(32)
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(15, 6, 15, 6)
        
        self.status_label = QLabel("● Disconnected")
        self.status_label.setFont(QFont("Inter", 11))
        status_layout.addWidget(self.status_label)
        
        left_layout.addWidget(self.status_frame)
        left_layout.addStretch()
        header_layout.addWidget(left_widget, 1)
        
        right_widget = QWidget()
        right_layout = QHBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        
        self.theme_btn = QPushButton("🌙")
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.setFont(QFont("Inter", 14))
        self.theme_btn.clicked.connect(self.toggle_theme)
        right_layout.addWidget(self.theme_btn)
        right_layout.addSpacing(10)
        
        server_label = QLabel("Server:")
        server_label.setFont(QFont("Inter", 11))
        right_layout.addWidget(server_label)
        
        self.ip_entry = QLineEdit("127.0.0.1")
        self.ip_entry.setFixedSize(140, 36)
        self.ip_entry.setFont(QFont("Inter", 11))
        right_layout.addWidget(self.ip_entry)
        
        pass_label = QLabel("Password(In server terminal):")
        pass_label.setFont(QFont("Inter", 11))
        right_layout.addWidget(pass_label)
        
        self.password_entry = QLineEdit()
        self.password_entry.setFixedSize(70, 36)
        self.password_entry.setFont(QFont("Inter", 11))
        self.password_entry.setMaxLength(4)
        self.password_entry.setPlaceholderText("XXXX")
        right_layout.addWidget(self.password_entry)
        
        name_label = QLabel("Name:")
        name_label.setFont(QFont("Inter", 11))
        right_layout.addWidget(name_label)
        
        self.name_entry = QLineEdit(os.getlogin() if hasattr(os, "getlogin") else "user")
        self.name_entry.setFixedSize(120, 36)
        self.name_entry.setFont(QFont("Inter", 11))
        right_layout.addWidget(self.name_entry)
        
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setFixedSize(110, 36)
        self.connect_btn.setFont(QFont("Inter", 12, QFont.Bold))
        self.connect_btn.clicked.connect(self.connect)
        right_layout.addWidget(self.connect_btn)
        
        header_layout.addWidget(right_widget)
        parent_layout.addWidget(self.header)
    
    def _create_video_area(self, parent_layout):
        self.video_container = QFrame()
        video_layout = QVBoxLayout(self.video_container)
        video_layout.setContentsMargins(12, 12, 12, 12)
        video_layout.setSpacing(10)
        
        self.video_scroll = QScrollArea()
        self.video_scroll.setWidgetResizable(True)
        self.video_scroll.setFrameShape(QFrame.NoFrame)
        self.video_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.video_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        self.video_widget = QWidget()
        self.video_layout = QHBoxLayout(self.video_widget)
        self.video_layout.setSpacing(12)
        self.video_layout.setContentsMargins(0, 0, 0, 0)
        self.video_layout.setAlignment(Qt.AlignLeft)
        self.video_scroll.setWidget(self.video_widget)
        
        video_layout.addWidget(self.video_scroll, 1)
        self._create_screen_panel(video_layout)
        # Add this to create the whiteboard overlay (add after creating video_container)
        self.whiteboard_canvas = WhiteboardCanvas()
        self.whiteboard_canvas.draw_action.connect(self.send_whiteboard_action)
        self.whiteboard_canvas.cursor_moved.connect(self.send_cursor_position_wb)

        # Create whiteboard overlay with toolbar
        self.whiteboard_overlay = QWidget()
        whiteboard_overlay_layout = QVBoxLayout(self.whiteboard_overlay)
        whiteboard_overlay_layout.setContentsMargins(0, 0, 0, 0)

        # Whiteboard toolbar
        wb_toolbar = QFrame()
        wb_toolbar.setFixedHeight(60)
        wb_toolbar_layout = QHBoxLayout(wb_toolbar)
        wb_toolbar_layout.setContentsMargins(15, 10, 15, 10)

        # Add toolbar buttons
        pen_btn = QPushButton("✏️ Pen")
        pen_btn.clicked.connect(lambda: self.set_whiteboard_tool("pen"))
        wb_toolbar_layout.addWidget(pen_btn)

        circle_btn = QPushButton("⭕ Circle")
        circle_btn.clicked.connect(lambda: self.set_whiteboard_tool("circle"))
        wb_toolbar_layout.addWidget(circle_btn)

        rect_btn = QPushButton("▭ Rect")
        rect_btn.clicked.connect(lambda: self.set_whiteboard_tool("rect"))
        wb_toolbar_layout.addWidget(rect_btn)

        line_btn = QPushButton("─ Line")
        line_btn.clicked.connect(lambda: self.set_whiteboard_tool("line"))
        wb_toolbar_layout.addWidget(line_btn)

        undo_btn = QPushButton("↶ Undo")
        undo_btn.clicked.connect(self.whiteboard_undo)
        wb_toolbar_layout.addWidget(undo_btn)

        clear_btn = QPushButton("🗑️ Clear")
        clear_btn.clicked.connect(self.whiteboard_clear)
        wb_toolbar_layout.addWidget(clear_btn)

        wb_toolbar_layout.addStretch()

        whiteboard_overlay_layout.addWidget(wb_toolbar)
        whiteboard_overlay_layout.addWidget(self.whiteboard_canvas, 1)

        self.whiteboard_overlay.setVisible(False)

        # Store reference to main video container
        self.video_main_container = self.video_container
        parent_layout.addWidget(self.video_container, 1)
    
    def _create_screen_panel(self, parent_layout):
        self.screen_panel = QFrame()
        self.screen_panel.setFixedHeight(60)
        screen_layout = QVBoxLayout(self.screen_panel)
        screen_layout.setContentsMargins(15, 10, 15, 10)
        screen_layout.setSpacing(10)
        
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.screen_label = QLabel("📺 Screen Share")
        self.screen_label.setFont(QFont("Inter", 13, QFont.Bold))
        header_layout.addWidget(self.screen_label)
        header_layout.addStretch()
        
        self.expand_btn = QPushButton("▼ Expand")
        self.expand_btn.setFixedSize(90, 28)
        self.expand_btn.setFont(QFont("Inter", 10))
        self.expand_btn.clicked.connect(self.toggle_screen_panel)
        header_layout.addWidget(self.expand_btn)
        
        screen_layout.addWidget(header_widget)
        
        self.screen_content = QLabel("No screen sharing active")
        self.screen_content.setAlignment(Qt.AlignCenter)
        self.screen_content.setFont(QFont("Inter", 12))
        self.screen_content.setMinimumHeight(350)
        self.screen_content.setVisible(False)
        screen_layout.addWidget(self.screen_content)
        
        self.screen_panel.setVisible(False)
        parent_layout.addWidget(self.screen_panel)
    
    def _create_sidebar(self, parent_layout):
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(380)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(15, 12, 15, 12)
        sidebar_layout.setSpacing(10)
        
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        header_label = QLabel("💬 Chat & More")
        header_label.setFont(QFont("Inter", 14, QFont.Bold))
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        
        sidebar_layout.addWidget(header_widget)
        
        self.tab_widget = QTabWidget()
        self.tab_widget.setFont(QFont("Inter", 11))
        
        self._create_chat_tab()
        self._create_participants_tab()
        self._create_files_tab()
        # Whiteboard tab removed - now a separate overlay
        
        sidebar_layout.addWidget(self.tab_widget, 1)
        parent_layout.addWidget(self.sidebar)
    
    def _create_chat_tab(self):
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(10, 10, 10, 10)
        chat_layout.setSpacing(10)
        
        chat_type_frame = QFrame()
        chat_type_frame.setFixedHeight(45)
        chat_type_layout = QHBoxLayout(chat_type_frame)
        chat_type_layout.setContentsMargins(15, 0, 12, 0)
        
        self.chat_type_label = QLabel("📢 Group Chat")
        self.chat_type_label.setFont(QFont("Inter", 12, QFont.Bold))
        chat_type_layout.addWidget(self.chat_type_label)
        chat_type_layout.addStretch()
        
        switch_btn = QPushButton("Switch to Direct")
        switch_btn.setFixedSize(110, 28)
        switch_btn.setFont(QFont("Inter", 10))
        switch_btn.clicked.connect(self.show_user_select)
        chat_type_layout.addWidget(switch_btn)
        
        chat_layout.addWidget(chat_type_frame)
        
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        self.chat_area.setFont(QFont("Inter", 10))
        chat_layout.addWidget(self.chat_area, 1)
        
        input_widget = QWidget()
        input_layout = QHBoxLayout(input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(8)
        
        self.msg_entry = QLineEdit()
        self.msg_entry.setPlaceholderText("Type a message...")
        self.msg_entry.setFixedHeight(42)
        self.msg_entry.setFont(QFont("Inter", 11))
        self.msg_entry.returnPressed.connect(self.send_chat)
        input_layout.addWidget(self.msg_entry, 1)
        
        send_btn = QPushButton("Send")
        send_btn.setFixedSize(70, 42)
        send_btn.setFont(QFont("Inter", 11, QFont.Bold))
        send_btn.clicked.connect(self.send_chat)
        input_layout.addWidget(send_btn)
        
        chat_layout.addWidget(input_widget)
        self.tab_widget.addTab(chat_widget, "💬 Chat")
    
    def _create_participants_tab(self):
        participants_widget = QWidget()
        participants_layout = QVBoxLayout(participants_widget)
        participants_layout.setContentsMargins(10, 15, 10, 10)
        participants_layout.setSpacing(10)
        
        label = QLabel("Participants in Meeting")
        label.setFont(QFont("Inter", 13, QFont.Bold))
        participants_layout.addWidget(label)
        
        self.users_list = QListWidget()
        self.users_list.setFont(QFont("Inter", 11))
        self.users_list.setMinimumHeight(200)
        
        # Make it non-selectable and non-editable
        self.users_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.users_list.setFocusPolicy(Qt.NoFocus)
        self.users_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        self.users_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {self.theme['panel']};
                color: {self.theme['text_primary']};
                border: 1px solid {self.theme['border']};
                border-radius: 12px;
                padding: 5px;
            }}
            QListWidget::item {{
                padding: 12px 8px;
                border-radius: 8px;
                margin: 2px;
                background-color: transparent;
            }}
            QListWidget::item:hover {{
                background-color: {self.theme['border']};
            }}
        """)
        
        # Add initial placeholder
        self.users_list.addItem("Connecting...")
        
        participants_layout.addWidget(self.users_list, 1)
        
        self.tab_widget.addTab(participants_widget, "👥 Participants")
        
        print("[DEBUG] Participants tab created")
    
    def test_user_display(self):
        """Test method to manually update user display"""
        print("[DEBUG] Manual test_user_display called")
        print(f"[DEBUG] self.active_users = {self.active_users}")
        self.update_users_signal.emit()
    
    def _create_files_tab(self):
        files_widget = QWidget()
        files_layout = QVBoxLayout(files_widget)
        files_layout.setContentsMargins(10, 15, 10, 10)
        files_layout.setSpacing(15)
        
        label = QLabel("File Sharing")
        label.setFont(QFont("Inter", 13, QFont.Bold))
        files_layout.addWidget(label)
        
        upload_btn = QPushButton("📤 Upload File")
        upload_btn.setFixedHeight(50)
        upload_btn.setFont(QFont("Inter", 13, QFont.Bold))
        upload_btn.clicked.connect(self.send_file)
        files_layout.addWidget(upload_btn)
        
        files_label = QLabel("Available Files:")
        files_label.setFont(QFont("Inter", 10))
        files_layout.addWidget(files_label)
        
        self.files_scroll = QScrollArea()
        self.files_scroll.setWidgetResizable(True)
        self.files_scroll.setFrameShape(QFrame.NoFrame)
        
        self.files_container = QWidget()
        self.files_layout = QVBoxLayout(self.files_container)
        self.files_layout.setSpacing(8)
        self.files_layout.setContentsMargins(5, 5, 5, 5)
        self.files_layout.setAlignment(Qt.AlignTop)  # ADD THIS LINE
        
        self.files_scroll.setWidget(self.files_container)
        
        files_layout.addWidget(self.files_scroll, 1)
        self.tab_widget.addTab(files_widget, "📁 Files")
    
    def _create_bottom_controls(self, parent_layout):
        controls = QFrame()
        controls.setFixedHeight(95)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.setSpacing(8)
        
        self.video_btn = QPushButton("🎥\nStart Video")
        self.video_btn.setFixedSize(110, 70)
        self.video_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.video_btn.setEnabled(False)
        self.video_btn.clicked.connect(self.toggle_video)
        button_layout.addWidget(self.video_btn)
        
        self.audio_btn = QPushButton("🎤\nStart Audio")
        self.audio_btn.setFixedSize(110, 70)
        self.audio_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.audio_btn.setEnabled(False)
        self.audio_btn.clicked.connect(self.toggle_audio)
        button_layout.addWidget(self.audio_btn)

        self.whiteboard_btn = QPushButton("🎨\nWhiteboard")
        self.whiteboard_btn.setFixedSize(110, 70)
        self.whiteboard_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.whiteboard_btn.setEnabled(False)
        self.whiteboard_btn.clicked.connect(self.toggle_whiteboard)
        button_layout.addWidget(self.whiteboard_btn)
        
        self.gesture_btn = QPushButton("✋\nGestures")
        self.gesture_btn.setFixedSize(110, 70)
        self.gesture_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.gesture_btn.setEnabled(False)
        self.gesture_btn.clicked.connect(self.toggle_gestures)
        button_layout.addWidget(self.gesture_btn)
        
        self.screen_btn = QPushButton("🖥️\nShare Screen")
        self.screen_btn.setFixedSize(110, 70)
        self.screen_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.screen_btn.setEnabled(False)
        self.screen_btn.clicked.connect(self.toggle_screen_share)
        button_layout.addWidget(self.screen_btn)
        
        self.view_screen_btn = QPushButton("👁\nView Screen")
        self.view_screen_btn.setFixedSize(110, 70)
        self.view_screen_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.view_screen_btn.setEnabled(False)
        self.view_screen_btn.clicked.connect(self.toggle_screen_view)
        button_layout.addWidget(self.view_screen_btn)
        
        self.leave_btn = QPushButton("📞\nLeave")
        self.leave_btn.setFixedSize(110, 70)
        self.leave_btn.setFont(QFont("Inter", 10, QFont.Bold))
        self.leave_btn.setEnabled(False)
        self.leave_btn.clicked.connect(self.leave_meeting)
        button_layout.addWidget(self.leave_btn)
        
        controls_layout.addStretch()
        controls_layout.addWidget(button_container)
        controls_layout.addStretch()
        
        parent_layout.addWidget(controls)
    
    def _apply_theme(self):
        style = f"""
            QMainWindow {{
                background-color: {self.theme['bg']};
            }}
            QFrame {{
                background-color: {self.theme['panel']};
                border-radius: 12px;
                border: 1px solid {self.theme['border']};
            }}
            QLabel {{
                color: {self.theme['text_primary']};
                background: transparent;
                border: none;
            }}
            QLineEdit {{
                background-color: {self.theme['panel']};
                color: {self.theme['text_primary']};
                border: 1px solid {self.theme['border']};
                border-radius: 8px;
                padding: 8px;
            }}
            QLineEdit:focus {{
                border: 1px solid {self.theme['primary']};
            }}
            QPushButton {{
                background-color: {self.theme['border']};
                color: {self.theme['text_primary']};
                border: none;
                border-radius: 8px;
                padding: 8px;
            }}
            QPushButton:hover {{
                background-color: {self.theme['primary']};
            }}
            QPushButton:disabled {{
                background-color: {self.theme['border']};
                color: {self.theme['text_tertiary']};
            }}
            QTextEdit {{
                background-color: {self.theme['panel']};
                color: {self.theme['text_primary']};
                border: 1px solid {self.theme['border']};
                border-radius: 12px;
                padding: 12px;
            }}
            QListWidget {{
                background-color: {self.theme['panel']};
                color: {self.theme['text_primary']};
                border: 1px solid {self.theme['border']};
                border-radius: 12px;
                padding: 5px;
            }}
            QListWidget::item {{
                padding: 8px;
                border-radius: 8px;
            }}
            QListWidget::item:selected {{
                background-color: {self.theme['primary']};
            }}
            QScrollArea {{
                background-color: {self.theme['bg']};
                border: none;
            }}
            QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            QTabBar::tab {{
                background-color: {self.theme['border']};
                color: {self.theme['text_primary']};
                padding: 10px 15px;
                border-radius: 8px;
                margin-right: 5px;
            }}
            QTabBar::tab:selected {{
                background-color: {self.theme['primary']};
            }}
        """
        
        self.setStyleSheet(style)
        self.header.setStyleSheet(f"background-color: {self.theme['panel']}; border: none; border-radius: 0px;")
        self.status_frame.setStyleSheet(f"background-color: {self.theme['border']}; border-radius: 20px; border: none;")
        self.video_container.setStyleSheet(f"background-color: {self.theme['panel']}; border-radius: 12px;")
        self.sidebar.setStyleSheet(f"background-color: {self.theme['panel']}; border-radius: 12px;")
        
        self.leave_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.theme['leave']};
                color: white;
                border-radius: 12px;
            }}
            QPushButton:hover {{
                background-color: #E85555;
            }}
        """)
    
    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.theme = DARK_THEME.copy() if self.dark_mode else LIGHT_THEME.copy()
        self.theme_btn.setText("🌙" if self.dark_mode else "☀️")
        self._apply_theme()
    
    def toggle_screen_panel(self):
        self.screen_expanded = not self.screen_expanded
        if self.screen_expanded:
            self.screen_panel.setFixedHeight(450)
            self.screen_content.setVisible(True)
            self.expand_btn.setText("▲ Collapse")
        else:
            self.screen_panel.setFixedHeight(60)
            self.screen_content.setVisible(False)
            self.expand_btn.setText("▼ Expand")
    
    def show_user_select(self):
        if not self.active_users:
            QMessageBox.information(self, "No Users", "No other users connected")
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Select User for Direct Chat")
        dialog.setFixedSize(340, 500)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setSpacing(8)
        
        group_btn = QPushButton("📢 Group Chat")
        group_btn.setFixedHeight(45)
        group_btn.setFont(QFont("Inter", 12, QFont.Bold))
        group_btn.clicked.connect(lambda: self.select_chat_user(None, dialog))
        dialog_layout.addWidget(group_btn)
        
        for user in self.active_users:
            if user != self.username:
                user_btn = QPushButton(f"👤 {user}")
                user_btn.setFixedHeight(42)
                user_btn.setFont(QFont("Inter", 11))
                user_btn.clicked.connect(lambda checked, u=user: self.select_chat_user(u, dialog))
                dialog_layout.addWidget(user_btn)
        
        dialog.exec_()
    
    def select_chat_user(self, user, dialog):
        self.selected_chat_user = user
        if user is None:
            self.chat_type_label.setText("📢 Group Chat")
        else:
            self.chat_type_label.setText(f"🔒 Chat with {user}")
        dialog.close()
    
    def log(self, text, is_system=False, is_private=False):
        self.log_signal.emit(text, is_system, is_private)
    
    def _handle_log(self, text, is_system, is_private):
        if is_system:
            self.chat_area.append(f'<span style="color: {self.theme["text_secondary"]}">[System] {text}</span>')
        elif is_private:
            self.chat_area.append(f'<span style="color: #FF9900">{text}</span>')
        else:
            self.chat_area.append(text)
    
    def _update_users_display(self):
        """Update the participants list display"""
        print(f"[DEBUG] _update_users_display called on thread: {threading.current_thread().name}")
        print(f"[DEBUG] Active users: {self.active_users}")
        print(f"[DEBUG] Current username: {self.username}")
        
        self.users_list.clear()
        
        if not self.active_users:
            print("[DEBUG] No active users to display")
            self.users_list.addItem("No participants yet")
            return
        
        for user in self.active_users:
            prefix = "👤 "
            if user == self.username:
                prefix = "👤 (You) "
            item_text = prefix + user
            print(f"[DEBUG] Adding user to list: {item_text}")
            self.users_list.addItem(item_text)
        
        print(f"[DEBUG] Users list now has {self.users_list.count()} items")
        
        # Force update
        self.users_list.viewport().update()
        self.users_list.repaint()
    
    def connect(self):
        global tcp_sock, server_ip
        if self.connected:
            return
        
        ip = self.ip_entry.text().strip()
        username = self.name_entry.text().strip()
        password = self.password_entry.text().strip()
        
        if not ip or not username or not password:
            QMessageBox.critical(self, "Error", "Please enter server IP, username, and password")
            return
        
        try:
            # Reset flags
            self.connected = False
            self.auth_failed = False
            
            # Create socket
            temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            temp_sock.settimeout(10)
            temp_sock.connect((ip, SERVER_TCP_PORT))
            server_ip = ip
            self.username = username
            
            print(f"[DEBUG] Connected to {ip}:{SERVER_TCP_PORT}")
            print(f"[DEBUG] Sending hello with password: {password}")
            
            hello_msg = pack_control({
                "type": "hello",
                "name": username,
                "password": password,
                "video_port": LOCAL_VIDEO_LISTEN_PORT,
                "audio_port": LOCAL_AUDIO_LISTEN_PORT
            })
            temp_sock.sendall(hello_msg)
            
            print("[DEBUG] Waiting for server response...")
            
            # Read response directly (before handing to receiver thread)
            buf = b""
            authenticated = False
            start_time = time.time()
            
            while time.time() - start_time < 5.0:
                try:
                    data = temp_sock.recv(4096)
                    if not data:
                        print("[DEBUG] Connection closed by server")
                        break
                    
                    print(f"[DEBUG] Received {len(data)} bytes")
                    buf += data
                    
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        
                        try:
                            msg = json.loads(line.decode())
                            mtype = msg.get("type")
                            print(f"[DEBUG] Received message type: {mtype}")
                            
                            if mtype == "error" and msg.get("auth_failed"):
                                print("[DEBUG] Authentication failed")
                                QMessageBox.critical(self, "Connection Failed", "Invalid password")
                                temp_sock.close()
                                return
                            
                            elif mtype == "whiteboard_sync":
                                print("[DEBUG] Received whiteboard_sync - authentication successful!")
                                authenticated = True
                                # Process this message
                                state = msg.get("state", {})
                                for stroke in state.get("strokes", []):
                                    self.whiteboard_canvas.add_remote_stroke(stroke)
                                for shape in state.get("shapes", []):
                                    self.whiteboard_canvas.add_remote_shape(shape)
                            
                            elif mtype == "user_list":
                                print(f"[DEBUG] Received user_list")
                                users = msg.get("users", [])
                                self.active_users = [u.get("name") for u in users]
                                self.update_users_signal.emit()
                                authenticated = True
                            
                            elif mtype == "join":
                                print(f"[DEBUG] Received join notification")
                                if msg.get("name") == username:
                                    authenticated = True
                            
                            # If we got whiteboard_sync or user_list, we're authenticated
                            if authenticated:
                                print("[DEBUG] Breaking out of receive loop - authenticated")
                                break
                                
                        except json.JSONDecodeError as e:
                            print(f"[DEBUG] JSON parse error: {e}")
                            continue
                    
                    if authenticated:
                        break
                        
                except socket.timeout:
                    print("[DEBUG] Socket timeout")
                    break
                except Exception as e:
                    print(f"[DEBUG] Receive error: {e}")
                    break
            
            if not authenticated:
                print(f"[DEBUG] Authentication failed or timeout")
                QMessageBox.critical(self, "Connection Failed", "Connection timeout. Server may be unreachable or password incorrect.")
                temp_sock.close()
                return
            
            # Successfully authenticated - now hand socket to receiver thread
            print("[DEBUG] Successfully authenticated, setting up connection")
            tcp_sock = temp_sock
            tcp_sock.settimeout(None)
            self.connected = True
            
            # Update UI
            print("[DEBUG] Updating UI for successful connection")
            self.connect_btn.setText("Connected")
            self.connect_btn.setEnabled(False)
            self.status_label.setText("● Connected")
            self._apply_theme()
            
            self.video_btn.setEnabled(True)
            self.audio_btn.setEnabled(True)
            self.screen_btn.setEnabled(True)
            self.view_screen_btn.setEnabled(True)
            self.whiteboard_btn.setEnabled(True)
            self.leave_btn.setEnabled(True)
            
            if self.GESTURE_AVAILABLE:
                self.gesture_btn.setEnabled(True)
            
            self.ip_entry.setEnabled(False)
            self.name_entry.setEnabled(False)
            self.password_entry.setEnabled(False)
            
            self.log(f"Connected as {username}", is_system=True)
            
        except Exception as e:
            print(f"[DEBUG] Connection exception: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Connection Failed", f"Error: {str(e)}")
            if 'temp_sock' in locals():
                try:
                    temp_sock.close()
                except:
                    pass
            self.username = None
    
    def leave_meeting(self):
        if not self.connected:
            return
        
        reply = QMessageBox.question(self, "Leave Meeting", 
                                     "Are you sure you want to leave?",
                                     QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            try:
                if tcp_sock:
                    tcp_sock.sendall(pack_control({"type": "bye"}))
            except:
                pass
            
            self.cleanup_connection()
            self.log("Left meeting", is_system=True)
    
    def cleanup_connection(self):
        global tcp_sock, screen_share_sock, screen_view_sock
        
        self.connected = False
        self.sending_video = False
        self.sending_audio = False
        self.gesture_enabled = False

        time.sleep(0.2)
        try:
            if self.audio_capture_stream:
                if self.audio_capture_stream.is_active():
                    self.audio_capture_stream.stop_stream()
                self.audio_capture_stream.close()
                self.audio_capture_stream = None
        except Exception as e:
            print(f"[DEBUG] Cleanup audio error: {e}")

    
        

        with self.screen_share_lock:
            self.sharing_screen = False
            self.viewing_screen = False
        
        try:
            if self.audio_capture_stream:
                self.audio_capture_stream.stop_stream()
                self.audio_capture_stream.close()
                self.audio_capture_stream = None
        except:
            pass
        
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.status_label.setText("● Disconnected")
        
        self.video_btn.setEnabled(False)
        self.video_btn.setText("🎥\nStart Video")
        self.audio_btn.setEnabled(False)
        self.audio_btn.setText("🎤\nStart Audio")
        self.gesture_btn.setEnabled(False)
        self.gesture_btn.setText("✋\nGestures")
        self.whiteboard_btn.setEnabled(False)
        self.whiteboard_btn.setText("🎨\nWhiteboard")
        self.screen_btn.setEnabled(False)
        self.screen_btn.setText("🖥️\nShare Screen")
        self.view_screen_btn.setEnabled(False)
        self.view_screen_btn.setText("👁\nView Screen")
        self.leave_btn.setEnabled(False)
        
        # Hide whiteboard if visible
        if self.whiteboard_visible:
            self.whiteboard_visible = False
            self.whiteboard_overlay.setVisible(False)
            self.video_main_container.setVisible(True)
        
        self.ip_entry.setEnabled(True)
        self.name_entry.setEnabled(True)
        self.password_entry.setEnabled(True)
        
        self._apply_theme()
        
        self.frames_by_src.clear()
        self.active_video_sources.clear()
        self.active_users = []
        self.selected_chat_user = None
        self.update_users_signal.emit()
        
        try:
            if tcp_sock:
                tcp_sock.close()
        except:
            pass
        tcp_sock = None
        
        try:
            if screen_share_sock:
                screen_share_sock.close()
        except:
            pass
        screen_share_sock = None
        
        try:
            if screen_view_sock:
                screen_view_sock.close()
        except:
            pass
        screen_view_sock = None
    
    def toggle_video(self):
        if not self.connected:
            return
        
        if not self.sending_video:
            try:
                self.video_cap = cv2.VideoCapture(0)
                if not self.video_cap.isOpened():
                    QMessageBox.critical(self, "Error", "Could not open camera")
                    return
                
                self.sending_video = True
                self.video_btn.setText("🎥\nStop Video")
                self.video_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {self.theme['success']};
                        color: white;
                        border-radius: 12px;
                    }}
                """)
                threading.Thread(target=self.video_sender_loop, daemon=True).start()
                self.log("Video started", is_system=True)
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Video error: {e}")
        else:
            self.sending_video = False
            if self.video_cap:
                self.video_cap.release()
                self.video_cap = None
            self.video_btn.setText("🎥\nStart Video")
            self.video_btn.setStyleSheet("")
            self.log("Video stopped", is_system=True)
    
    def toggle_audio(self):
        if not self.connected or not PYAUDIO_AVAILABLE:
            return
        
        if not self.sending_audio:
            try:
                self.audio_capture_stream = self.pa.open(
                    format=AUDIO_FORMAT,
                    channels=AUDIO_CHANNELS,
                    rate=AUDIO_RATE,
                    input=True,
                    frames_per_buffer=AUDIO_INPUT_CHUNK,
                    stream_callback=None  # Using blocking mode
                )
                
                # Start the stream
                self.audio_capture_stream.start_stream()
                
                self.sending_audio = True
                self.audio_btn.setText("🎤\nStop Audio")
                self.audio_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {self.theme['success']};
                        color: white;
                        border-radius: 12px;
                    }}
                """)
                threading.Thread(target=self.audio_sender_loop, daemon=True).start()
                self.log("Audio started", is_system=True)
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Audio error: {e}")
                self.sending_audio = False
        else:
            # Stop audio - proper cleanup order is critical
            self.sending_audio = False  # Signal threads to stop first
            
            # Wait a moment for sender thread to finish
            time.sleep(0.1)
            
            # Now close the stream
            if self.audio_capture_stream:
                try:
                    if self.audio_capture_stream.is_active():
                        self.audio_capture_stream.stop_stream()
                    self.audio_capture_stream.close()
                except Exception as e:
                    print(f"[DEBUG] Error closing audio stream: {e}")
                finally:
                    self.audio_capture_stream = None
            
            self.audio_btn.setText("🎤\nStart Audio")
            self.audio_btn.setStyleSheet("")
            self.log("Audio stopped", is_system=True)
    
    def toggle_gestures(self):
        if not self.connected or not self.GESTURE_AVAILABLE:
            return
        
        if not self.gesture_enabled:
            if not self.sending_video:
                QMessageBox.information(self, "Video Required", "Please start video first")
                return
            
            self.gesture_enabled = True
            self.gesture_btn.setText("✋\nStop Gestures")
            self.gesture_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.theme['success']};
                    color: white;
                    border-radius: 12px;
                }}
            """)
            self.log("Gesture recognition enabled", is_system=True)
        else:
            self.gesture_enabled = False
            self.gesture_btn.setText("✋\nGestures")
            self.gesture_btn.setStyleSheet("")
            self.log("Gesture recognition disabled", is_system=True)
    
    def detect_gesture(self, frame):
        """Enhanced gesture detection with better recognition"""
        if not self.gesture_enabled or not self.GESTURE_AVAILABLE:
            return None
        
        # Cooldown check
        if time.time() - self.gesture_cooldown < 2.0:
            return None
        
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(frame_rgb)
        
        if not results.multi_hand_landmarks:
            return None
        
        num_hands = len(results.multi_hand_landmarks)
        
        # Two hands detected - check for heart or clap
        if num_hands >= 2:
            hand1 = results.multi_hand_landmarks[0]
            hand2 = results.multi_hand_landmarks[1]
            
            # Get thumb tips and index finger tips
            h1_thumb_tip = hand1.landmark[4]
            h1_index_tip = hand1.landmark[8]
            h2_thumb_tip = hand2.landmark[4]
            h2_index_tip = hand2.landmark[8]
            
            # Calculate distance between thumbs and between index fingers
            thumb_distance = abs(h1_thumb_tip.x - h2_thumb_tip.x) + abs(h1_thumb_tip.y - h2_thumb_tip.y)
            index_distance = abs(h1_index_tip.x - h2_index_tip.x) + abs(h1_index_tip.y - h2_index_tip.y)
            
            # HEART: Thumbs and index fingers close together (forming heart shape)
            if thumb_distance < 0.15 and index_distance < 0.15:
                # Check if fingers are pointing downward (heart orientation)
                avg_tip_y = (h1_thumb_tip.y + h2_thumb_tip.y + h1_index_tip.y + h2_index_tip.y) / 4
                h1_wrist_y = hand1.landmark[0].y
                h2_wrist_y = hand2.landmark[0].y
                avg_wrist_y = (h1_wrist_y + h2_wrist_y) / 2
                
                if avg_tip_y < avg_wrist_y:  # Tips above wrists
                    self.gesture_cooldown = time.time()
                    return "heart"
            
            # CLAP: Palms facing each other and close together
            # Check if hands are close (within clapping distance)
            h1_palm = hand1.landmark[9]  # Middle of palm
            h2_palm = hand2.landmark[9]
            palm_distance = abs(h1_palm.x - h2_palm.x) + abs(h1_palm.y - h2_palm.y)
            
            if palm_distance < 0.2:
                # Check if all fingertips of both hands are extended
                h1_fingers_up = sum([
                    hand1.landmark[8].y < hand1.landmark[6].y,   # Index
                    hand1.landmark[12].y < hand1.landmark[10].y, # Middle
                    hand1.landmark[16].y < hand1.landmark[14].y, # Ring
                    hand1.landmark[20].y < hand1.landmark[18].y  # Pinky
                ])
                
                h2_fingers_up = sum([
                    hand2.landmark[8].y < hand2.landmark[6].y,
                    hand2.landmark[12].y < hand2.landmark[10].y,
                    hand2.landmark[16].y < hand2.landmark[14].y,
                    hand2.landmark[20].y < hand2.landmark[18].y
                ])
                
                # If both hands have most fingers up and are close = clap
                if h1_fingers_up >= 3 and h2_fingers_up >= 3:
                    self.gesture_cooldown = time.time()
                    return "clap"
        
        # Single hand gestures
        for hand_landmarks in results.multi_hand_landmarks:
            # Get landmark positions
            thumb_tip = hand_landmarks.landmark[4]
            thumb_ip = hand_landmarks.landmark[3]
            index_tip = hand_landmarks.landmark[8]
            index_pip = hand_landmarks.landmark[6]
            middle_tip = hand_landmarks.landmark[12]
            middle_pip = hand_landmarks.landmark[10]
            ring_tip = hand_landmarks.landmark[16]
            ring_pip = hand_landmarks.landmark[14]
            pinky_tip = hand_landmarks.landmark[20]
            pinky_pip = hand_landmarks.landmark[18]
            wrist = hand_landmarks.landmark[0]
            
            # THUMBS UP: Thumb extended up, other fingers curled
            thumb_up = thumb_tip.y < thumb_ip.y < wrist.y
            fingers_down = (
                index_tip.y > index_pip.y and
                middle_tip.y > middle_pip.y and
                ring_tip.y > ring_pip.y and
                pinky_tip.y > pinky_pip.y
            )
            
            if thumb_up and fingers_down:
                self.gesture_cooldown = time.time()
                return "thumbs_up"
            
            # PEACE SIGN: Index and middle fingers up, others down
            index_up = index_tip.y < index_pip.y < wrist.y
            middle_up = middle_tip.y < middle_pip.y < wrist.y
            ring_down = ring_tip.y > ring_pip.y
            pinky_down = pinky_tip.y > pinky_pip.y
            
            # Check if index and middle are separated (V shape)
            finger_separation = abs(index_tip.x - middle_tip.x)
            
            if index_up and middle_up and ring_down and pinky_down and finger_separation > 0.05:
                self.gesture_cooldown = time.time()
                return "peace"
            
            # WAVE: All fingers extended
            fingers_up = sum([
                index_tip.y < index_pip.y,
                middle_tip.y < middle_pip.y,
                ring_tip.y < ring_pip.y,
                pinky_tip.y < pinky_pip.y
            ])
            
            if fingers_up >= 4:
                self.gesture_cooldown = time.time()
                return "wave"
        
        return None
    
    def _show_gesture(self, from_user, gesture_type):
        emoji_map = {
            "heart": "❤️",
            "thumbs_up": "👍",
            "peace": "✌️",
            "wave": "👋",
            "clap": "👏"
        }
        
        emoji = emoji_map.get(gesture_type)
        if not emoji:
            return  # Don't show anything for unknown gestures
        
        self.log(f"{from_user} sent {emoji}", is_system=True)
        
        # Show floating emoji
        floating = FloatingEmoji(emoji, self.video_container)
        floating.show()
    
    def set_whiteboard_tool(self, tool):
        self.whiteboard_canvas.set_tool(tool)
    
    def whiteboard_undo(self):
        self.whiteboard_canvas.undo()
        if tcp_sock and self.connected:
            tcp_sock.sendall(pack_control({
                "type": "whiteboard_action",
                "action": "undo"
            }))
    
    def whiteboard_clear(self):
        self.whiteboard_canvas.clear_canvas()
        if tcp_sock and self.connected:
            tcp_sock.sendall(pack_control({
                "type": "whiteboard_action",
                "action": "clear"
            }))
    
    def send_whiteboard_action(self, action_data):
        if tcp_sock and self.connected:
            msg = {
                "type": "whiteboard_action",
                "action": action_data["action"],
                "data": action_data.get("data")
            }
            tcp_sock.sendall(pack_control(msg))
    
    def _handle_whiteboard_action(self, action_data):
        action = action_data.get("action")
        
        if action == "draw":
            self.whiteboard_canvas.add_remote_stroke(action_data.get("data"))
        elif action == "shape":
            self.whiteboard_canvas.add_remote_shape(action_data.get("data"))
        elif action == "erase":
            self.whiteboard_canvas.erase_element(action_data.get("erase_id"))
        elif action == "clear":
            self.whiteboard_canvas.clear_canvas()
        elif action == "undo":
            self.whiteboard_canvas.undo()
    
    def toggle_whiteboard(self):
        """Toggle whiteboard overlay"""
        if not self.connected:
            return
        
        self.whiteboard_visible = not self.whiteboard_visible
        
        if self.whiteboard_visible:
            # Show whiteboard, hide videos
            self.video_main_container.setVisible(False)
            self.whiteboard_overlay.setVisible(True)
            self.whiteboard_btn.setText("🎨\nHide Board")
            self.whiteboard_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.theme['success']};
                    color: white;
                    border-radius: 12px;
                }}
            """)
            self.log("Whiteboard enabled", is_system=True)
        else:
            # Hide whiteboard, show videos
            self.whiteboard_overlay.setVisible(False)
            self.video_main_container.setVisible(True)
            self.whiteboard_btn.setText("🎨\nWhiteboard")
            self.whiteboard_btn.setStyleSheet("")
            self.log("Whiteboard disabled", is_system=True)
    
    def send_cursor_position_wb(self, x, y):
        """Send cursor position for whiteboard"""
        if self.connected and tcp_sock and self.whiteboard_visible:
            try:
                tcp_sock.sendall(pack_control({
                    "type": "cursor_move",
                    "x": x,
                    "y": y
                }))
            except:
                pass
    
    def _update_remote_cursor(self, username, x, y, color):
        self.whiteboard_canvas.update_remote_cursor(username, x, y, color)
    
    def toggle_screen_share(self):
        global screen_share_sock, server_ip
        
        with self.screen_share_lock:
            if not self.sharing_screen:
                if not MSS_AVAILABLE:
                    QMessageBox.critical(self, "Error", "mss library not installed")
                    return
                
                try:
                    print(f"[DEBUG] Creating new screen share socket to {server_ip}:{SCREEN_TCP_PORT}")
                    screen_share_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    screen_share_sock.settimeout(10)
                    screen_share_sock.connect((server_ip, SCREEN_TCP_PORT))
                    print(f"[DEBUG] Connected to screen share port")
                    
                    role_msg = {"role": "presenter"}
                    print(f"[DEBUG] Sending role message: {role_msg}")
                    if not write_msg(screen_share_sock, role_msg):
                        print("[DEBUG] Failed to send role message")
                        screen_share_sock.close()
                        screen_share_sock = None
                        QMessageBox.critical(self, "Error", "Failed to initiate screen share")
                        return
                    
                    print("[DEBUG] Waiting for response from server...")
                    response = read_msg(screen_share_sock)
                    print(f"[DEBUG] Server response: {response}")
                    
                    if not response:
                        print("[DEBUG] No response received from server")
                        screen_share_sock.close()
                        screen_share_sock = None
                        QMessageBox.critical(self, "Error", "Screen share denied: No response from server")
                        return
                        
                    if response.get("status") != "ok":
                        reason = response.get("reason", "Unknown")
                        print(f"[DEBUG] Server denied connection: {reason}")
                        screen_share_sock.close()
                        screen_share_sock = None
                        QMessageBox.critical(self, "Error", f"Screen share denied: {reason}")
                        return
                    
                    print("[DEBUG] Screen share accepted by server")
                    screen_share_sock.settimeout(None)
                    self.sharing_screen = True
                    self.screen_btn.setText("🖥️\nStop Sharing")
                    self.screen_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {self.theme['success']};
                            color: white;
                            border-radius: 12px;
                        }}
                    """)
                    tcp_sock.sendall(pack_control({"type": "present_start"}))
                    threading.Thread(target=self.screen_share_thread, daemon=True).start()
                    self.log("Started screen sharing", is_system=True)
                    
                except Exception as e:
                    print(f"[DEBUG] Exception in screen share start: {e}")
                    import traceback
                    traceback.print_exc()
                    QMessageBox.critical(self, "Error", f"Screen share error: {e}")
                    if screen_share_sock:
                        try:
                            screen_share_sock.close()
                        except:
                            pass
                        screen_share_sock = None
            else:
                print("[DEBUG] Stopping screen share")
                # Stop sharing - force immediate cleanup
                self.sharing_screen = False
                
                # Close socket FIRST, before UI updates
                temp_sock = screen_share_sock
                screen_share_sock = None  # Clear global immediately
                
                if temp_sock:
                    try:
                        # Set socket to non-blocking to avoid hangs
                        temp_sock.setblocking(False)
                        # Try to send disconnect
                        try:
                            disconnect_msg = json.dumps({"type": "disconnect"}).encode()
                            length = struct.pack('!I', len(disconnect_msg))
                            temp_sock.sendall(length + disconnect_msg)
                            print("[DEBUG] Sent disconnect message")
                        except Exception as e:
                            print(f"[DEBUG] Could not send disconnect: {e}")
                        
                        # Force close immediately
                        temp_sock.close()
                        print("[DEBUG] Screen share socket closed")
                    except Exception as e:
                        print(f"[DEBUG] Error closing screen socket: {e}")
                
                # Update UI
                self.screen_btn.setText("🖥️\nShare Screen")
                self.screen_btn.setStyleSheet("")
                
                # Send present_stop notification
                try:
                    tcp_sock.sendall(pack_control({"type": "present_stop"}))
                    print("[DEBUG] Sent present_stop notification")
                except Exception as e:
                    print(f"[DEBUG] Error sending present_stop: {e}")
                
                # Wait a moment to ensure server processes the disconnect
                time.sleep(0.5)
                
                self.log("Stopped screen sharing", is_system=True)
    
    def screen_share_thread(self):
        import mss
        import base64
        
        print("[DEBUG] Screen share thread starting")
        
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            last_send = time.time()
            
            while self.sharing_screen and self.connected:
                # Check if socket is still valid
                if screen_share_sock is None:
                    print("[DEBUG] Socket is None, exiting thread")
                    break
                    
                try:
                    now = time.time()
                    if now - last_send < (1.0 / SCREEN_FPS):
                        time.sleep(0.01)
                        continue
                    
                    screenshot = sct.grab(monitor)
                    img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)
                    img = img.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.LANCZOS)
                    
                    buffer = io.BytesIO()
                    img.save(buffer, format='JPEG', quality=SCREEN_QUALITY)
                    img_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
                    
                    frame_msg = {"type": "screen_frame", "data": img_data}
                    if not write_msg(screen_share_sock, frame_msg):
                        print("[DEBUG] Failed to send screen frame, breaking")
                        break
                    
                    last_send = now
                    
                except Exception as e:
                    print(f"[DEBUG] Screen share thread exception: {e}")
                    break
        
        print("[DEBUG] Screen share thread exiting")
        
        # Don't update UI here - already done in toggle_screen_share
    
    def toggle_screen_view(self):
        global screen_view_sock, server_ip
        
        with self.screen_share_lock:
            if not self.viewing_screen:
                try:
                    screen_view_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    screen_view_sock.settimeout(10)
                    screen_view_sock.connect((server_ip, SCREEN_TCP_PORT))
                    
                    role_msg = {"role": "viewer"}
                    if not write_msg(screen_view_sock, role_msg):
                        screen_view_sock.close()
                        screen_view_sock = None
                        QMessageBox.critical(self, "Error", "Failed to connect")
                        return
                    
                    response = read_msg(screen_view_sock)
                    if not response or response.get("status") != "ok":
                        screen_view_sock.close()
                        screen_view_sock = None
                        QMessageBox.information(self, "Info", "No active screen share")
                        return
                    
                    screen_view_sock.settimeout(None)
                    self.viewing_screen = True
                    self.view_screen_btn.setText("👁\nStop Viewing")
                    self.view_screen_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {self.theme['success']};
                            color: white;
                            border-radius: 12px;
                        }}
                    """)
                    
                    if not self.screen_expanded:
                        self.toggle_screen_panel()
                    
                    self.screen_panel.setVisible(True)
                    
                    threading.Thread(target=self.screen_view_thread, daemon=True).start()
                    self.log("Viewing screen share", is_system=True)
                    
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Screen view error: {e}")
                    if screen_view_sock:
                        try:
                            screen_view_sock.close()
                        except:
                            pass
                        screen_view_sock = None
            else:
                self.viewing_screen = False
                self.view_screen_btn.setText("👁\nView Screen")
                self.view_screen_btn.setStyleSheet("")
                
                if screen_view_sock:
                    try:
                        screen_view_sock.close()
                    except:
                        pass
                    screen_view_sock = None
                
                self.update_screen_signal.emit(None)
                self.log("Stopped viewing screen share", is_system=True)
    
    def screen_view_thread(self):
        import base64
        
        while self.viewing_screen and self.connected:
            try:
                frame_msg = read_msg(screen_view_sock)
                if not frame_msg:
                    break
                
                if frame_msg.get("type") == "screen_frame":
                    img_data = frame_msg.get("data")
                    if img_data:
                        img_bytes = base64.b64decode(img_data)
                        img = Image.open(io.BytesIO(img_bytes))
                        
                        max_width = 900
                        max_height = 350
                        img_width, img_height = img.size
                        aspect_ratio = img_width / img_height
                        
                        if img_width > max_width:
                            img_width = max_width
                            img_height = int(img_width / aspect_ratio)
                        
                        if img_height > max_height:
                            img_height = max_height
                            img_width = int(img_height * aspect_ratio)
                        
                        img = img.resize((img_width, img_height), Image.LANCZOS)
                        self.update_screen_signal.emit(img)
                
                elif frame_msg.get("type") == "present_stop":
                    break
                    
            except Exception as e:
                print(f"Screen view error: {e}")
                break
        
        with self.screen_share_lock:
            self.viewing_screen = False
        
        self.update_screen_signal.emit(None)
    
    def _update_screen_display(self, img):
        if img is None:
            self.screen_content.setText("No screen sharing active")
            self.screen_content.setPixmap(QPixmap())
        else:
            img_rgb = img.convert('RGB')
            data = img_rgb.tobytes('raw', 'RGB')
            qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            self.screen_content.setPixmap(pixmap)
            self.screen_content.setText("")
    
    def video_sender_loop(self):
        while self.sending_video and self.connected:
            try:
                ret, frame = self.video_cap.read()
                if not ret:
                    time.sleep(0.01)
                    continue
                
                if self.gesture_enabled:
                    gesture = self.detect_gesture(frame)
                    if gesture:
                        try:
                            tcp_sock.sendall(pack_control({
                                "type": "gesture",
                                "gesture_type": gesture
                            }))
                            self.gesture_signal.emit(self.username, gesture)
                        except:
                            pass
                
                frame = cv2.resize(frame, (VIDEO_WIDTH, VIDEO_HEIGHT))
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                frame_data = buffer.tobytes()
                
                seq = 0
                offset = 0
                while offset < len(frame_data):
                    chunk_size = min(VIDEO_CHUNK, len(frame_data) - offset)
                    chunk = frame_data[offset:offset + chunk_size]
                    header = struct.pack('!II', seq, len(frame_data))
                    packet = header + chunk
                    video_send_sock.sendto(packet, (server_ip, SERVER_VIDEO_UDP_PORT))
                    seq += 1
                    offset += chunk_size
                
                time.sleep(1.0 / VIDEO_FPS)
                
            except Exception as e:
                print(f"Video sender error: {e}")
                break
    
    def audio_sender_loop(self):
        while self.sending_audio and self.connected:
            try:
                # Add thread safety check
                if not self.audio_capture_stream:
                    time.sleep(0.01)
                    continue
                
                # Check if stream is still active before reading
                try:
                    if not self.audio_capture_stream.is_active():
                        time.sleep(0.01)
                        continue
                except:
                    # Stream might be closed
                    break
                
                # Read with exception handling and smaller buffer
                try:
                    data = self.audio_capture_stream.read(
                        AUDIO_INPUT_CHUNK, 
                        exception_on_overflow=False
                    )
                    if data and len(data) > 0:
                        audio_send_sock.sendto(data, (server_ip, SERVER_AUDIO_UDP_PORT))
                except IOError as e:
                    # Handle buffer overflow or underrun
                    print(f"[DEBUG] Audio read error (recoverable): {e}")
                    time.sleep(0.01)
                    continue
                except Exception as e:
                    print(f"[DEBUG] Audio sender error: {e}")
                    break
                    
            except Exception as e:
                print(f"[DEBUG] Audio sender loop error: {e}")
                time.sleep(0.1)
    
    def video_receiver_loop(self):
        frame_buffers = {}
        
        while self.running:
            try:
                data, addr = video_recv_sock.recvfrom(MAX_UDP_SIZE)
                if len(data) < 12:
                    continue
                
                src_ip_bytes = data[:4]
                src_ip = socket.inet_ntoa(src_ip_bytes)
                seq, total_size = struct.unpack('!II', data[4:12])
                chunk = data[12:]
                
                if src_ip not in frame_buffers:
                    frame_buffers[src_ip] = {"data": b"", "total": total_size, "seq": 0}
                
                buf = frame_buffers[src_ip]
                
                if seq == 0:
                    buf["data"] = b""
                    buf["total"] = total_size
                    buf["seq"] = 0
                
                if seq == buf["seq"]:
                    buf["data"] += chunk
                    buf["seq"] += 1
                
                if len(buf["data"]) >= buf["total"]:
                    try:
                        frame_data = buf["data"][:buf["total"]]
                        nparr = np.frombuffer(frame_data, np.uint8)
                        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        
                        if frame is not None:
                            self.frames_by_src[src_ip] = frame
                            self.active_video_sources[src_ip] = time.time()
                    except:
                        pass
                    
                    frame_buffers[src_ip] = {"data": b"", "total": 0, "seq": 0}
                    
            except Exception as e:
                time.sleep(0.001)
    
    def audio_receiver_loop(self):
        if not PYAUDIO_AVAILABLE:
            return
        
        while self.running:
            try:
                data, addr = audio_recv_sock.recvfrom(8192)
                if data and len(data) > 0:
                    if not self.audio_buffer.full():
                        self.audio_buffer.put(data)
            except:
                time.sleep(0.001)
    
    def audio_playback_loop(self):
        if not PYAUDIO_AVAILABLE:
            return
        
        try:
            self.audio_play_stream = self.pa.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE,
                output=True,
                frames_per_buffer=AUDIO_CHUNK
            )
        except:
            return
        
        while self.running:
            try:
                if not self.audio_buffer.empty():
                    data = self.audio_buffer.get()
                    self.audio_play_stream.write(data)
                else:
                    time.sleep(0.001)
            except:
                time.sleep(0.001)
    
    def video_cleanup_loop(self):
        while self.running:
            try:
                time.sleep(0.5)
                current_time = time.time()
                stale_sources = []
                
                for src_ip, last_time in list(self.active_video_sources.items()):
                    if current_time - last_time > self.video_timeout:
                        stale_sources.append(src_ip)
                
                if stale_sources:
                    for src_ip in stale_sources:
                        self.active_video_sources.pop(src_ip, None)
                        self.frames_by_src.pop(src_ip, None)
                        
            except Exception as e:
                time.sleep(0.1)
    
    def _redraw_video(self):
        for i in reversed(range(self.video_layout.count())):
            widget = self.video_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        
        active_sources = list(self.frames_by_src.keys())
        num_videos = len(active_sources)
        
        if num_videos == 0:
            label = QLabel("No video feeds")
            label.setAlignment(Qt.AlignCenter)
            label.setFont(QFont("Inter", 14))
            label.setStyleSheet(f"color: {self.theme['text_secondary']};")
            self.video_layout.addWidget(label)
            return
        
        own_video_ip = None
        other_videos = []
        
        for src_ip in active_sources:
            if src_ip == self.local_ip:
                own_video_ip = src_ip
            else:
                other_videos.append(src_ip)
        
        if num_videos == 1:
            self._create_video_tile(active_sources[0], 1050, 700, is_main=True)
        elif num_videos == 2:
            if own_video_ip and other_videos:
                self._create_video_tile(other_videos[0], 850, 600, is_main=True)
                self._create_video_tile(own_video_ip, 280, 210, is_main=False)
            else:
                for src_ip in active_sources:
                    self._create_video_tile(src_ip, 500, 400)
        elif num_videos == 3:
            if own_video_ip and len(other_videos) >= 1:
                self._create_video_tile(other_videos[0], 700, 500, is_main=True)
                if len(other_videos) >= 2:
                    self._create_video_tile(other_videos[1], 280, 210)
                self._create_video_tile(own_video_ip, 280, 210)
            else:
                for src_ip in active_sources:
                    self._create_video_tile(src_ip, 350, 280)
        else:
            for src_ip in active_sources:
                is_own = (src_ip == own_video_ip)
                self._create_video_tile(src_ip, 340, 280, is_own_video=is_own)
    
    def _create_video_tile(self, src_ip, width, height, is_main=False, is_own_video=False):
        tile = QFrame()
        tile.setFixedSize(width, height)
        tile.setStyleSheet(f"""
            QFrame {{
                background-color: {self.theme['panel']};
                border: {'2px' if is_main else '1px'} solid {self.theme['border']};
                border-radius: 12px;
            }}
        """)
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        tile_layout.setSpacing(0)
        
        label_text = f"👤 {src_ip}"
        if is_own_video or src_ip == self.local_ip:
            label_text = f"👤 You ({src_ip})"
        
        user_label = QLabel(label_text)
        label_height = 32 if not is_main else 40
        user_label.setFixedHeight(label_height)
        user_label.setFont(QFont("Inter", 10 if not is_main else 12, QFont.Bold))
        user_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        user_label.setStyleSheet(f"""
            background-color: {self.theme['border']};
            color: {self.theme['text_primary']};
            padding-left: 10px;
            border-radius: 0px;
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
        """)
        tile_layout.addWidget(user_label)
        
        video_label = QLabel()
        video_label.setAlignment(Qt.AlignCenter)
        video_label.setStyleSheet("background-color: black;")
        
        frame = self.frames_by_src.get(src_ip)
        if frame is not None:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = frame_rgb.shape
            bytes_per_line = ch * w
            qt_image = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qt_image).scaled(
                width, 
                height - label_height, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            video_label.setPixmap(pixmap)
        
        tile_layout.addWidget(video_label)
        self.video_layout.addWidget(tile)
    
    def tcp_receiver_loop(self):
        global tcp_sock
        
        print("[DEBUG] TCP receiver thread started")
        
        while self.running:
            if not tcp_sock:
                time.sleep(0.2)
                continue
            
            buf = b""
            while self.connected or tcp_sock:
                try:
                    data = tcp_sock.recv(4096)
                    if not data:
                        print("[DEBUG] TCP connection closed by server")
                        QTimer.singleShot(0, self.cleanup_connection)
                        break
                    
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        
                        try:
                            msg = json.loads(line.decode())
                            mtype = msg.get('type', 'unknown')
                            print(f"[DEBUG] Received message type: {mtype}")
                            
                            # IMPORTANT: Process message immediately on this thread
                            # then use signals to update UI
                            self._process_message(msg)
                            
                        except Exception as e:
                            print(f"[DEBUG] JSON parse error: {e}")
                            continue
                            
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.connected:
                        print(f"[DEBUG] TCP exception: {e}")
                        QTimer.singleShot(0, self.cleanup_connection)
                    break

    def _process_message(self, msg):
        """Process message on receiver thread, use signals for UI updates"""
        mtype = msg.get("type")
        print(f"[DEBUG] _process_message: {mtype}")
        
        if mtype == "error":
            error_msg = msg.get("message", "Unknown error")
            self.log_signal.emit(f"⚠ {error_msg}", True, False)
            
            if msg.get("auth_failed"):
                self.auth_failed = True
                QTimer.singleShot(100, self.cleanup_connection)
        
        elif mtype == "chat":
            frm = msg.get("from")
            text = msg.get("message")
            self.log_signal.emit(f"{frm}: {text}", False, False)
        
        elif mtype == "private_chat":
            frm = msg.get("from")
            text = msg.get("message")
            self.log_signal.emit(f"🔒 {frm} (private): {text}", False, True)
        
        elif mtype == "private_chat_sent":
            to = msg.get("to")
            text = msg.get("message")
            self.log_signal.emit(f"🔒 You to {to}: {text}", False, True)
        
        elif mtype == "user_list":
            print(f"[DEBUG] Processing user_list")
            users = msg.get("users", [])
            print(f"[DEBUG] Users received: {users}")
            
            # Update active_users list
            self.active_users = [u.get("name") for u in users if u.get("name")]
            print(f"[DEBUG] Active users now: {self.active_users}")
            
            # Trigger UI update
            self.update_users_signal.emit()
            
            # Mark as connected
            if not self.connected and self.username and self.username in self.active_users:
                print(f"[DEBUG] Setting connected=True")
                self.connected = True
        
        elif mtype == "join":
            name = msg.get('name')
            self.log_signal.emit(f"→ {name} joined", True, False)
        
        elif mtype == "leave":
            name = msg.get('name')
            addr = msg.get('addr')
            self.log_signal.emit(f"← {name} left", True, False)
            
            if addr:
                self.frames_by_src.pop(addr, None)
                self.active_video_sources.pop(addr, None)
        
        elif mtype == "gesture":
            frm = msg.get("from")
            gesture_type = msg.get("gesture_type")
            self.gesture_signal.emit(frm, gesture_type)
        
        elif mtype == "whiteboard_sync":
            print(f"[DEBUG] Processing whiteboard_sync")
            if not self.connected:
                self.connected = True
            
            state = msg.get("state", {})
            for stroke in state.get("strokes", []):
                self.whiteboard_canvas.add_remote_stroke(stroke)
            for shape in state.get("shapes", []):
                self.whiteboard_canvas.add_remote_shape(shape)
        
        elif mtype == "whiteboard_action":
            self.whiteboard_signal.emit(msg)
        
        elif mtype == "cursor_move":
            username = msg.get("from")
            x = msg.get("x")
            y = msg.get("y")
            color = msg.get("color", "#4C88FF")
            self.cursor_signal.emit(username, x, y, color)
        
        elif mtype == "file_offer":
            frm = msg.get("from")
            fname = msg.get("filename")
            size = msg.get("size")
            
            print(f"[FILE_DEBUG] Received file_offer: {fname} from {frm}, size: {size}")
            
            # Use QMetaObject to ensure it runs on main thread
            from PyQt5.QtCore import QMetaObject, Qt as QtCore
            QMetaObject.invokeMethod(
                self,
                "_add_file_card_slot",
                QtCore.QueuedConnection,
                Q_ARG(str, fname),
                Q_ARG(int, size),
                Q_ARG(str, frm)
            )
            
            # Show notification in chat
            size_mb = size / (1024 * 1024)
            self.log_signal.emit(f"📁 {frm} shared: {fname} ({size_mb:.2f} MB)", True, False)

        elif mtype == "present_start":
            presenter = msg.get('from')
            self.log_signal.emit(f"🖥 {presenter} started presenting", True, False)
            QTimer.singleShot(0, lambda: self.screen_label.setText(f"📺 {presenter} is presenting"))
            QTimer.singleShot(0, lambda: self.screen_panel.setVisible(True))
            
            if not self.screen_expanded:
                QTimer.singleShot(0, self.toggle_screen_panel)
        
        elif mtype == "present_stop":
            presenter = msg.get('from', 'Someone')
            self.log_signal.emit(f"🖥 {presenter} stopped presenting", True, False)
            QTimer.singleShot(0, lambda: self.screen_label.setText("📺 Screen Share"))
            QTimer.singleShot(0, lambda: self.screen_content.setText("No screen sharing active"))
            
            if self.viewing_screen:
                QTimer.singleShot(0, self.toggle_screen_view)
            
            if self.screen_expanded:
                QTimer.singleShot(0, self.toggle_screen_panel)
        
    def _add_file_card(self, filename, size, from_user):
        """Add a file card to the files list - INTERNAL USE ONLY"""
        print(f"[FILE_DEBUG] _add_file_card START: {filename}")
        print(f"[FILE_DEBUG] Current thread: {threading.current_thread().name}")
        print(f"[FILE_DEBUG] files_layout exists: {hasattr(self, 'files_layout')}")
        print(f"[FILE_DEBUG] files_layout count BEFORE: {self.files_layout.count()}")
        
        # Check if file already exists
        for i in range(self.files_layout.count()):
            item = self.files_layout.itemAt(i)
            if item:
                widget = item.widget()
                if widget and hasattr(widget, 'filename') and widget.filename == filename:
                    print(f"[FILE_DEBUG] File {filename} already exists, skipping")
                    return
        
        print(f"[FILE_DEBUG] Creating file card widget...")
        
        file_card = QFrame()
        file_card.filename = filename  # Store for duplicate checking
        file_card.setFixedHeight(70)
        file_card.setStyleSheet(f"""
            QFrame {{
                background-color: {self.theme['border']};
                border: 1px solid {self.theme['border']};
                border-radius: 8px;
                margin: 2px;
            }}
            QFrame:hover {{
                background-color: {self.theme['primary']};
            }}
        """)
        
        card_layout = QHBoxLayout(file_card)
        card_layout.setContentsMargins(15, 10, 15, 10)
        
        # File info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(3)
        
        name_label = QLabel(f"📄 {filename}")
        name_label.setFont(QFont("Inter", 11, QFont.Bold))
        name_label.setStyleSheet(f"color: {self.theme['text_primary']}; border: none; background: transparent;")
        info_layout.addWidget(name_label)
        
        size_mb = size / (1024 * 1024)
        detail_label = QLabel(f"From: {from_user} | Size: {size_mb:.2f} MB")
        detail_label.setFont(QFont("Inter", 9))
        detail_label.setStyleSheet(f"color: {self.theme['text_secondary']}; border: none; background: transparent;")
        info_layout.addWidget(detail_label)
        
        card_layout.addLayout(info_layout, 1)
        
        # Download button
        download_btn = QPushButton("⬇ Download")
        download_btn.setFixedSize(100, 40)
        download_btn.setFont(QFont("Inter", 10, QFont.Bold))
        download_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.theme['success']};
                color: white;
                border-radius: 6px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #2AB875;
            }}
        """)
        download_btn.clicked.connect(lambda checked=False, fn=filename: self.start_file_download(fn))
        card_layout.addWidget(download_btn)
        print(f"[FILE_DEBUG] Adding widget to layout at position 0...")
    
        # Insert at top of list
        self.files_layout.insertWidget(0, file_card)
        
        print(f"[FILE_DEBUG] files_layout count AFTER: {self.files_layout.count()}")
        
        # Force updates
        file_card.show()
        self.files_container.updateGeometry()
        self.files_container.update()
        self.files_scroll.viewport().update()
        self.files_scroll.update()
        
        # Switch to Files tab
        self.tab_widget.setCurrentIndex(2)
        
        print(f"[FILE_DEBUG] _add_file_card COMPLETE")
    
    def start_file_download(self, filename):
        """Start file download with confirmation"""
        print(f"[FILE_DEBUG] start_file_download called: {filename}")
        
        reply = QMessageBox.question(
            self,
            "Download File",
            f"Download {filename}?\n\nIt will be saved to:\n{DOWNLOADS_DIR}",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.log(f"📥 Downloading {filename}...", is_system=True)
            threading.Thread(target=self.download_file, args=(filename,), daemon=True).start()
        else:
            print(f"[FILE_DEBUG] Download cancelled by user")

    # Fix: Use a closure to capture filename correctly
    # download_btn.clicked.connect(lambda checked=False, fn=filename: self.start_file_download(fn))
    # card_layout.addWidget(download_btn)
    
    def download_file_with_confirm(self, filename):
        """Download file with user confirmation"""
        reply = QMessageBox.question(
            self,
            "Download File",
            f"Download {filename} to Downloads folder?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.log(f"📥 Downloading {filename}...", is_system=True)
            threading.Thread(target=self.download_file, args=(filename,), daemon=True).start()


    def download_file(self, filename):
        try:
            print(f"[FILE_DEBUG] download_file START: {filename}")
            
            file_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_sock.settimeout(30.0)
            file_sock.connect((server_ip, FILE_TCP_PORT))
            
            request = json.dumps({"type": "file_download", "filename": filename}).encode()
            file_sock.sendall(request)
            
            info_data = file_sock.recv(4096)
            if info_data == b"ERROR":
                self.log(f"❌ File not found: {filename}", is_system=True)
                file_sock.close()
                return
            
            info = json.loads(info_data.decode().strip())
            size = info["size"]
            file_sock.sendall(b"READY")
            
            dest = os.path.join(DOWNLOADS_DIR, filename)
            remaining = size
            
            print(f"[FILE_DEBUG] Downloading to: {dest}")
            
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = file_sock.recv(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            
            file_sock.close()
            
            print(f"[FILE_DEBUG] Download complete: {filename}")
            self.log(f"✅ Downloaded: {filename} → {DOWNLOADS_DIR}", is_system=True)
            
            # Show success message
            QTimer.singleShot(0, lambda: QMessageBox.information(
                self, 
                "Download Complete", 
                f"File downloaded successfully!\n\n{filename}\n\nSaved to: {DOWNLOADS_DIR}"
            ))
            
        except Exception as e:
            print(f"[FILE_DEBUG] Download error: {e}")
            self.log(f"❌ Download error: {e}", is_system=True)
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self,
                "Download Failed",
                f"Failed to download {filename}\n\nError: {str(e)}"
            ))

    
    def send_chat(self):
        global tcp_sock
        
        if not tcp_sock or not self.connected:
            QMessageBox.critical(self, "Not connected", "Connect first")
            return
        
        text = self.msg_entry.text().strip()
        if not text:
            return
        
        try:
            if self.selected_chat_user is None:
                tcp_sock.sendall(pack_control({"type": "chat", "message": text}))
                self.log(f"You: {text}")
            else:
                tcp_sock.sendall(pack_control({
                    "type": "private_chat",
                    "to": self.selected_chat_user,
                    "message": text
                }))
            
            self.msg_entry.clear()
            
        except Exception as e:
            self.log(f"✗ Send failed: {e}", is_system=True)
            self.cleanup_connection()
    
    def send_file(self):
        if not self.connected:
            QMessageBox.critical(self, "Not connected", "Connect first")
            return
        
        path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if not path:
            return
        
        threading.Thread(target=self._upload_file, args=(path,), daemon=True).start()
    
    def _upload_file(self, path):
        try:
            name = os.path.basename(path)
            size = os.path.getsize(path)
            
            self.log(f"📤 Uploading {name}...", is_system=True)
            
            file_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_sock.settimeout(60.0)
            file_sock.connect((server_ip, FILE_TCP_PORT))
            
            header = json.dumps({
                "type": "file_upload",
                "filename": name,
                "size": size,
                "from": self.username
            }).encode()
            file_sock.sendall(header)
            
            response = file_sock.recv(10)
            if response != b"READY":
                self.log(f"❌ Upload failed", is_system=True)
                file_sock.close()
                return
            
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    file_sock.sendall(chunk)
            
            file_sock.recv(10)
            file_sock.close()
            
            # Only log success locally - server will broadcast to everyone
            self.log(f"✅ Upload complete: {name}", is_system=True)
            
        except Exception as e:
            self.log(f"❌ Upload error: {e}", is_system=True)
    
    def closeEvent(self, event):
        self.running = False
        try:
            if self.connected and tcp_sock:
                tcp_sock.sendall(pack_control({"type": "bye"}))
        except:
            pass
        
        self.cleanup_connection()
        
        try:
            if self.audio_play_stream:
                self.audio_play_stream.stop_stream()
                self.audio_play_stream.close()
        except:
            pass
        
        if self.pa:
            self.pa.terminate()
        
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Inter", 10))
    
    window = ConferenceClient()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

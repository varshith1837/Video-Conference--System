"""
Enhanced Conference Client - MINIMALISTIC BLACK & WHITE VERSION
PERFORMANCE OPTIMIZED for multi-client scenarios
"""

import socket
import threading
import json
import struct
import time
import os
import io
import random
import math
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image, ImageTk, ImageDraw
import cv2
import numpy as np
import queue as Queue

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

# OPTIMIZED SETTINGS for your i3 laptop
VIDEO_WIDTH = 320
VIDEO_HEIGHT = 240
VIDEO_FPS = 15  # Reduced from 20 to 15 FPS
VIDEO_CHUNK = 1100
JPEG_QUALITY = 70  # Reduced from 80 to 70 for better performance

AUDIO_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None
AUDIO_CHUNK = 256
AUDIO_INPUT_CHUNK = 256
MAX_UDP_SIZE = 65507

SCREEN_WIDTH = 640  # Reduced from 800
SCREEN_HEIGHT = 360  # Reduced from 450
SCREEN_FPS = 8  # Reduced from 10
SCREEN_QUALITY = 40  # Reduced from 50

REACTION_DURATION_SECONDS = 2.0

# PERFORMANCE TUNING
MAX_FRAME_BUFFER = 3  # Limit frame buffer size
GUI_UPDATE_INTERVAL = 0.05  # 20 FPS for GUI (was 30ms = 33 FPS)
FRAME_DROP_THRESHOLD = 5  # Drop frames if queue gets too large

# ====== Framing Utilities ======
def write_msg(sock, obj):
    try:
        data = json.dumps(obj).encode('utf-8')
        length = struct.pack('!I', len(data))
        sock.sendall(length + data)
        return True
    except:
        return False

def read_msg(sock):
    try:
        sock.settimeout(15.0)
        length_data = sock.recv(4)
        if not length_data or len(length_data) < 4:
            return None
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
    except socket.timeout:
        print("read_msg: socket timeout")
        return None
    except Exception as e:
        print(f"read_msg error: {e}")
        return None

def pack_control(obj):
    return (json.dumps(obj) + "\n").encode()

# ====== Networking Sockets ======
video_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_recv_sock.bind(("0.0.0.0", LOCAL_VIDEO_LISTEN_PORT))
audio_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_recv_sock.bind(("0.0.0.0", LOCAL_AUDIO_LISTEN_PORT))
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "ConferenceFiles")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# ====== MINIMALISTIC BLACK & WHITE COLOR SCHEME ======
COLORS = {
    'bg_primary': '#000000',
    'bg_secondary': '#1a1a1a',
    'bg_tertiary': '#0d0d0d',
    'border': '#2d2d2d',
    'border_light': '#404040',
    'text_primary': '#ffffff',
    'text_secondary': '#cccccc',
    'text_muted': '#808080',
    'hover': '#333333',
    'hover_light': '#404040',
    'active': '#ffffff',
    'active_text': '#000000',
}

# ====== Modern Conference Client ======
class ModernConferenceClient:
    def __init__(self, master):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.master = master
        self.master.title("Conference")
        self.master.geometry("1400x900")
        self.master.configure(fg_color=COLORS['bg_primary'])
        
        self.username = None
        self.server_ip = None
        self.connected = False
        self.sending_video = False
        self.sending_audio = False
        
        self.screen_share_lock = threading.Lock()
        self.sharing_screen = False
        self.viewing_screen = False
        self.screen_maximized = False
        
        self.tcp_sock = None
        self.sharing_screen_sock = None
        self.viewing_screen_sock = None
        
        # OPTIMIZED: Use locks for thread-safe access
        self.frames_lock = threading.Lock()
        self.frames_by_src = {}
        self.video_frames = {}
        self.screen_frame = None
        
        self.active_users = []
        self.active_users_dict = {}
        self.video_states = {}
        self.my_addr = None
        self.active_reactions = {}
        
        # OPTIMIZED: Smaller GUI queue
        self.gui_queue = Queue.Queue(maxsize=10)
        
        self.pa = pyaudio.PyAudio() if PYAUDIO_AVAILABLE else None
        self.audio_play_stream = None
        self.audio_capture_stream = None
        self.audio_buffer = Queue.Queue(maxsize=20)  # Reduced from 30
        
        self.running = True
        self.last_canvas_update = 0
        self.canvas_update_interval = GUI_UPDATE_INTERVAL
        
        # OPTIMIZED: Track pending GUI update
        self.gui_update_pending = False
        
        self._build_ui()
        
        threading.Thread(target=self.tcp_receiver_loop, daemon=True).start()
        threading.Thread(target=self.video_receiver_loop, daemon=True).start()
        threading.Thread(target=self.audio_receiver_loop, daemon=True).start()
        threading.Thread(target=self.audio_playback_loop, daemon=True).start()
        
        self.process_gui_queue()
        self.reaction_updater_loop()
        self.log(f"Files save to: {DOWNLOADS_DIR}", "system")

    def _build_ui(self):
        # Main container
        self.main_container = ctk.CTkFrame(self.master, fg_color=COLORS['bg_primary'])
        self.main_container.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Top Bar
        self.top_bar = ctk.CTkFrame(self.main_container, height=50, corner_radius=0,
                                    fg_color=COLORS['bg_secondary'], border_width=1,
                                    border_color=COLORS['border'])
        self.top_bar.pack(fill="x", padx=0, pady=0)
        self.top_bar.pack_propagate(False)
        
        title_label = ctk.CTkLabel(self.top_bar, text="Conference",
                                   font=ctk.CTkFont(size=16, weight="normal"),
                                   text_color=COLORS['text_primary'])
        title_label.pack(side="left", padx=20)
        
        # Connection controls
        conn_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        conn_frame.pack(side="right", padx=15, pady=8)
        
        self.ip_entry = ctk.CTkEntry(conn_frame, placeholder_text="Server IP", width=130,
                                     height=34, fg_color=COLORS['bg_tertiary'],
                                     border_color=COLORS['border'], border_width=1,
                                     text_color=COLORS['text_primary'])
        self.ip_entry.pack(side="left", padx=4)
        self.ip_entry.insert(0, "127.0.0.1")
        
        self.name_entry = ctk.CTkEntry(conn_frame, placeholder_text="Name", width=100,
                                       height=34, fg_color=COLORS['bg_tertiary'],
                                       border_color=COLORS['border'], border_width=1,
                                       text_color=COLORS['text_primary'])
        self.name_entry.pack(side="left", padx=4)
        self.name_entry.insert(0, os.getlogin() if hasattr(os, "getlogin") else "user")
        
        self.connect_btn = ctk.CTkButton(conn_frame, text="Connect", width=90, height=34,
                                         command=self.connect, corner_radius=4,
                                         fg_color=COLORS['text_primary'],
                                         hover_color=COLORS['text_secondary'],
                                         text_color=COLORS['bg_primary'],
                                         border_width=0,
                                         font=ctk.CTkFont(size=13))
        self.connect_btn.pack(side="left", padx=4)
        
        self.leave_btn = ctk.CTkButton(conn_frame, text="Leave", width=80, height=34,
                                       command=self.leave_meeting, corner_radius=4,
                                       fg_color=COLORS['bg_tertiary'],
                                       hover_color=COLORS['hover'],
                                       text_color=COLORS['text_primary'],
                                       border_width=1, border_color=COLORS['border'],
                                       state="disabled",
                                       font=ctk.CTkFont(size=13))
        self.leave_btn.pack(side="left", padx=4)
        
        # Status
        status_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        status_frame.pack(side="right", padx=15)
        
        self.status_indicator = ctk.CTkLabel(status_frame, text="●",
                                            font=ctk.CTkFont(size=12),
                                            text_color=COLORS['text_muted'])
        self.status_indicator.pack(side="left", padx=3)
        
        self.status_label = ctk.CTkLabel(status_frame, text="Disconnected",
                                        font=ctk.CTkFont(size=12),
                                        text_color=COLORS['text_secondary'])
        self.status_label.pack(side="left", padx=3)
        
        self.users_label = ctk.CTkLabel(status_frame, text="0 users",
                                       font=ctk.CTkFont(size=12),
                                       text_color=COLORS['text_muted'])
        self.users_label.pack(side="left", padx=10)
        
        # Content Area
        content_frame = ctk.CTkFrame(self.main_container, fg_color=COLORS['bg_primary'])
        content_frame.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Left Panel - Video Area
        left_panel = ctk.CTkFrame(content_frame, fg_color=COLORS['bg_primary'])
        left_panel.pack(side="left", fill="both", expand=True, padx=0, pady=0)
        
        canvas_container = ctk.CTkFrame(left_panel, fg_color=COLORS['bg_secondary'],
                                       corner_radius=0, border_width=0)
        canvas_container.pack(fill="both", expand=True, padx=0, pady=0)
        
        self.canvas = tk.Canvas(canvas_container, bg=COLORS['bg_primary'],
                               highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=0, pady=0)
        
        # ZOOM-STYLE BOTTOM CONTROL BAR
        controls_bar = ctk.CTkFrame(left_panel, height=80, corner_radius=0,
                                   fg_color=COLORS['bg_secondary'],
                                   border_width=1, border_color=COLORS['border'])
        controls_bar.pack(fill="x", side="bottom", padx=0, pady=0)
        controls_bar.pack_propagate(False)
        
        btn_container = ctk.CTkFrame(controls_bar, fg_color="transparent")
        btn_container.pack(expand=True)
        
        # Video Button
        video_frame = ctk.CTkFrame(btn_container, fg_color="transparent")
        video_frame.pack(side="left", padx=8, pady=15)
        
        self.video_btn = ctk.CTkButton(video_frame, text="📹 Video", width=110, height=45,
                                       corner_radius=6,
                                       font=ctk.CTkFont(size=13),
                                       command=self.toggle_video,
                                       fg_color=COLORS['bg_tertiary'],
                                       hover_color=COLORS['hover'],
                                       text_color=COLORS['text_primary'],
                                       border_width=1, border_color=COLORS['border'])
        self.video_btn.pack()
        
        # Audio Button
        audio_frame = ctk.CTkFrame(btn_container, fg_color="transparent")
        audio_frame.pack(side="left", padx=8, pady=15)
        
        self.audio_btn = ctk.CTkButton(audio_frame, text="🎤 Audio", width=110, height=45,
                                       corner_radius=6,
                                       font=ctk.CTkFont(size=13),
                                       command=self.toggle_audio,
                                       fg_color=COLORS['bg_tertiary'],
                                       hover_color=COLORS['hover'],
                                       text_color=COLORS['text_primary'],
                                       border_width=1, border_color=COLORS['border'])
        self.audio_btn.pack()
        
        # Screen Share Button
        screen_frame = ctk.CTkFrame(btn_container, fg_color="transparent")
        screen_frame.pack(side="left", padx=8, pady=15)
        
        self.screen_btn = ctk.CTkButton(screen_frame, text="🖥️ Share", width=110, height=45,
                                        corner_radius=6,
                                        font=ctk.CTkFont(size=13),
                                        command=self.toggle_screen_share,
                                        fg_color=COLORS['bg_tertiary'],
                                        hover_color=COLORS['hover'],
                                        text_color=COLORS['text_primary'],
                                        border_width=1, border_color=COLORS['border'])
        self.screen_btn.pack()
        
        # View Screen Button
        view_frame = ctk.CTkFrame(btn_container, fg_color="transparent")
        view_frame.pack(side="left", padx=8, pady=15)
        
        self.view_screen_btn = ctk.CTkButton(view_frame, text="👁️ View", width=110, height=45,
                                            corner_radius=6,
                                            font=ctk.CTkFont(size=13),
                                            command=self.toggle_screen_view,
                                            fg_color=COLORS['bg_tertiary'],
                                            hover_color=COLORS['hover'],
                                            text_color=COLORS['text_primary'],
                                            border_width=1, border_color=COLORS['border'])
        self.view_screen_btn.pack()
        
        # Right Sidebar
        right_panel = ctk.CTkFrame(content_frame, width=380, corner_radius=0,
                                  fg_color=COLORS['bg_secondary'],
                                  border_width=1, border_color=COLORS['border'])
        right_panel.pack(side="right", fill="y", padx=0, pady=0)
        right_panel.pack_propagate(False)
        
        # Tabview
        self.tabview = ctk.CTkTabview(right_panel, corner_radius=0,
                                      fg_color=COLORS['bg_secondary'],
                                      segmented_button_fg_color=COLORS['bg_tertiary'],
                                      segmented_button_selected_color=COLORS['hover'],
                                      segmented_button_selected_hover_color=COLORS['hover_light'],
                                      segmented_button_unselected_color=COLORS['bg_tertiary'],
                                      segmented_button_unselected_hover_color=COLORS['hover'],
                                      text_color=COLORS['text_primary'])
        self.tabview.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Chat Tab
        chat_tab = self.tabview.add("Chat")
        
        target_frame = ctk.CTkFrame(chat_tab, fg_color="transparent")
        target_frame.pack(fill="x", pady=(10, 10), padx=10)
        
        ctk.CTkLabel(target_frame, text="To:", font=ctk.CTkFont(size=12),
                    text_color=COLORS['text_secondary']).pack(side="left", padx=5)
        
        self.chat_target = ctk.CTkComboBox(target_frame, values=["Everyone"], width=150,
                                          fg_color=COLORS['bg_tertiary'],
                                          button_color=COLORS['hover'],
                                          button_hover_color=COLORS['hover_light'],
                                          border_color=COLORS['border'],
                                          text_color=COLORS['text_primary'])
        self.chat_target.pack(side="left", padx=5)
        self.chat_target.set("Everyone")
        
        chat_display_frame = ctk.CTkFrame(chat_tab, fg_color=COLORS['bg_tertiary'],
                                         corner_radius=4, border_width=1,
                                         border_color=COLORS['border'])
        chat_display_frame.pack(fill="both", expand=True, pady=(0, 10), padx=10)
        
        self.chat_text = ctk.CTkTextbox(chat_display_frame, wrap="word",
                                        font=ctk.CTkFont(size=12),
                                        fg_color=COLORS['bg_tertiary'],
                                        text_color=COLORS['text_primary'],
                                        border_width=0)
        self.chat_text.pack(fill="both", expand=True, padx=2, pady=2)
        
        # Emoji Reactions
        emoji_frame = ctk.CTkFrame(chat_tab, fg_color="transparent")
        emoji_frame.pack(fill="x", pady=(0, 10), padx=10)
        
        reactions = ["😂", "🔥", "❤️", "😭", "🙏", "👍", "😎", "👌", "🥳", "💯"]
        for i, emoji in enumerate(reactions):
            btn = ctk.CTkButton(emoji_frame, text=emoji, width=32, height=32,
                              corner_radius=4, font=ctk.CTkFont(size=16),
                              command=lambda e=emoji: self.send_emoji_reaction(e),
                              fg_color=COLORS['bg_tertiary'],
                              hover_color=COLORS['hover'],
                              border_width=1, border_color=COLORS['border'])
            btn.grid(row=0, column=i, padx=2, pady=2)
        
        # Chat input
        input_frame = ctk.CTkFrame(chat_tab, fg_color="transparent")
        input_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        self.msg_entry = ctk.CTkEntry(input_frame, placeholder_text="Type a message...",
                                      height=38, corner_radius=4,
                                      fg_color=COLORS['bg_tertiary'],
                                      border_color=COLORS['border'], border_width=1,
                                      text_color=COLORS['text_primary'])
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.msg_entry.bind('<Return>', lambda e: self.send_chat())
        
        ctk.CTkButton(input_frame, text="Send", width=70, height=38,
                     corner_radius=4, command=self.send_chat,
                     fg_color=COLORS['text_primary'],
                     hover_color=COLORS['text_secondary'],
                     text_color=COLORS['bg_primary'],
                     font=ctk.CTkFont(size=13)).pack(side="right")
        
        # Users Tab
        users_tab = self.tabview.add("Users")
        
        users_display_frame = ctk.CTkFrame(users_tab, fg_color=COLORS['bg_tertiary'],
                                          corner_radius=4, border_width=1,
                                          border_color=COLORS['border'])
        users_display_frame.pack(fill="both", expand=True, pady=10, padx=10)
        
        self.users_textbox = ctk.CTkTextbox(users_display_frame, wrap="word",
                                           font=ctk.CTkFont(size=13),
                                           fg_color=COLORS['bg_tertiary'],
                                           text_color=COLORS['text_primary'],
                                           border_width=0)
        self.users_textbox.pack(fill="both", expand=True, padx=2, pady=2)
        
        # Files Tab
        files_tab = self.tabview.add("Files")
        
        ctk.CTkButton(files_tab, text="📤 Send File", height=45,
                     corner_radius=4, command=self.send_file,
                     fg_color=COLORS['text_primary'],
                     hover_color=COLORS['text_secondary'],
                     text_color=COLORS['bg_primary'],
                     font=ctk.CTkFont(size=14)).pack(fill="x", pady=10, padx=10)
        
        ctk.CTkButton(files_tab, text="📂 Open Downloads", height=45,
                     corner_radius=4, command=self.open_downloads,
                     fg_color=COLORS['bg_tertiary'],
                     hover_color=COLORS['hover'],
                     text_color=COLORS['text_primary'],
                     border_width=1, border_color=COLORS['border'],
                     font=ctk.CTkFont(size=14)).pack(fill="x", pady=10, padx=10)
        
        # Activity Log
        log_frame = ctk.CTkFrame(files_tab, fg_color=COLORS['bg_tertiary'],
                                corner_radius=4, border_width=1,
                                border_color=COLORS['border'])
        log_frame.pack(fill="both", expand=True, pady=(10, 0), padx=10)
        
        ctk.CTkLabel(log_frame, text="Activity Log",
                    font=ctk.CTkFont(size=13),
                    text_color=COLORS['text_secondary']).pack(pady=8)
        
        self.log_textbox = ctk.CTkTextbox(log_frame, wrap="word",
                                         font=ctk.CTkFont(size=11),
                                         fg_color=COLORS['bg_tertiary'],
                                         text_color=COLORS['text_secondary'],
                                         border_width=0)
        self.log_textbox.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    def reaction_updater_loop(self):
        if not self.running:
            return
        now = time.time()
        needs_redraw = False
        
        if len(self.active_reactions) > 0:
            needs_redraw = True
        
        expired_keys = [
            addr for addr, reaction in self.active_reactions.items()
            if now - reaction["timestamp"] > REACTION_DURATION_SECONDS
        ]
        
        for key in expired_keys:
            self.active_reactions.pop(key, None)
            needs_redraw = True
        
        if needs_redraw:
            self.schedule_canvas_update()
        
        self.master.after(100, self.reaction_updater_loop)

    # OPTIMIZED: Prevent multiple simultaneous canvas updates
    def schedule_canvas_update(self):
        current_time = time.time()
        if current_time - self.last_canvas_update >= self.canvas_update_interval:
            if not self.gui_update_pending:
                self.gui_update_pending = True
                try:
                    self.gui_queue.put_nowait(self.redraw_canvas)
                except Queue.Full:
                    self.gui_update_pending = False

    def send_emoji_reaction(self, emoji):
        if not self.connected:
            return
        self.send_reaction_message(emoji)

    def send_reaction_message(self, emoji):
        if not self.tcp_sock or not self.connected or not self.my_addr:
            return
        try:
            self.active_reactions[self.my_addr] = {"emoji": emoji, "timestamp": time.time()}
            self.schedule_canvas_update()
            self.tcp_sock.sendall(pack_control({
                "type": "reaction",
                "emoji": emoji,
                "addr": self.my_addr
            }))
        except Exception as e:
            self.log(f"Send reaction failed: {e}", "error")
            try:
                self.gui_queue.put_nowait(self.cleanup_connection)
            except:
                pass

    def send_chat_message(self, message):
        if not self.tcp_sock or not self.connected:
            return
        try:
            target = self.chat_target.get()
            if target == "Everyone":
                self.tcp_sock.sendall(pack_control({"type": "chat", "message": message}))
                self.log(f"You: {message}", "own_chat")
            else:
                self.tcp_sock.sendall(pack_control({"type": "private_chat", "to": target, "message": message}))
        except Exception as e:
            self.log(f"Send failed: {e}", "error")
            try:
                self.gui_queue.put_nowait(self.cleanup_connection)
            except:
                pass

    # OPTIMIZED: Process queue less frequently
    def process_gui_queue(self):
        processed = 0
        max_per_cycle = 5  # Process max 5 items per cycle
        
        try:
            while processed < max_per_cycle:
                callback = self.gui_queue.get_nowait()
                if callable(callback):
                    callback()
                processed += 1
        except Queue.Empty:
            pass
        finally:
            if self.running:
                self.master.after(30, self.process_gui_queue)  # Check every 30ms

    def update_users_display(self):
        self.users_textbox.delete("1.0", "end")
        if self.username:
            self.users_textbox.insert("end", f"● {self.username} (You)\n")
        if not self.active_users_dict:
            if not self.username:
                self.users_textbox.insert("end", "No users connected\n")
        else:
            for user in self.active_users_dict:
                name = user.get("name", "Unknown")
                addr = user.get("addr", "")
                self.users_textbox.insert("end", f"● {name}")
                if addr:
                    self.users_textbox.insert("end", f" ({addr})")
                self.users_textbox.insert("end", "\n")
        
        total_users = len(self.active_users_dict) + (1 if self.connected else 0)
        self.users_label.configure(text=f"{total_users} user{'s' if total_users != 1 else ''}")

    def open_downloads(self):
        import platform, subprocess
        try:
            if platform.system() == "Windows":
                os.startfile(DOWNLOADS_DIR)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", DOWNLOADS_DIR])
            else:
                subprocess.Popen(["xdg-open", DOWNLOADS_DIR])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder: {e}")

    def connect(self):
        if self.connected:
            messagebox.showinfo("Info", "Already connected")
            return
        
        self.server_ip = self.ip_entry.get().strip()
        if not self.server_ip:
            messagebox.showerror("Error", "Enter server IP")
            return
        
        username = self.name_entry.get().strip() or "user"
        
        try:
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.connect((self.server_ip, SERVER_TCP_PORT))
            hello = {"type": "hello", "name": username,
                    "video_port": LOCAL_VIDEO_LISTEN_PORT,
                    "audio_port": LOCAL_AUDIO_LISTEN_PORT}
            self.tcp_sock.sendall(pack_control(hello))
            
            self.connected = True
            self.username = username
            self.status_label.configure(text="Connected")
            self.status_indicator.configure(text_color=COLORS['text_primary'])
            self.leave_btn.configure(state="normal")
            self.connect_btn.configure(state="disabled")
            
            if not self.audio_play_stream and PYAUDIO_AVAILABLE:
                self.start_audio_playback()
            
            self.log(f"Connected as {username}", "success")
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))

    def leave_meeting(self):
        if not self.connected:
            return
        if messagebox.askyesno("Leave Meeting", "Leave meeting?"):
            try:
                if self.tcp_sock:
                    self.tcp_sock.sendall(pack_control({"type": "bye"}))
            except:
                pass
            self.cleanup_connection()
            self.log("Left meeting", "system")

    def cleanup_connection(self):
        self.connected = False
        self.sending_video = False
        self.sending_audio = False
        
        self._cleanup_share_socket()
        self._cleanup_view_socket()
        
        try:
            if self.audio_capture_stream:
                self.audio_capture_stream.stop_stream()
                self.audio_capture_stream.close()
                self.audio_capture_stream = None
        except:
            pass
        
        self.status_label.configure(text="Disconnected")
        self.status_indicator.configure(text_color=COLORS['text_muted'])
        self.leave_btn.configure(state="disabled")
        self.connect_btn.configure(state="normal")
        
        self.video_btn.configure(fg_color=COLORS['bg_tertiary'],
                                hover_color=COLORS['hover'])
        self.audio_btn.configure(fg_color=COLORS['bg_tertiary'],
                                hover_color=COLORS['hover'])
        self.screen_btn.configure(fg_color=COLORS['bg_tertiary'],
                                 hover_color=COLORS['hover'])
        self.view_screen_btn.configure(fg_color=COLORS['bg_tertiary'],
                                      hover_color=COLORS['hover'])
        
        with self.frames_lock:
            self.frames_by_src.clear()
            self.video_frames.clear()
        
        self.screen_frame = None
        self.active_users = []
        self.active_users_dict = {}
        self.video_states = {}
        self.active_reactions.clear()
        self.my_addr = None
        
        self.chat_target.configure(values=["Everyone"])
        self.chat_target.set("Everyone")
        self.update_users_display()
        
        try:
            if self.tcp_sock:
                self.tcp_sock.close()
                self.tcp_sock = None
        except:
            pass
        
        time.sleep(0.5)
        self.schedule_canvas_update()

    def on_close(self):
        self.running = False
        try:
            if self.connected and self.tcp_sock:
                self.tcp_sock.sendall(pack_control({"type": "bye"}))
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
        self.master.destroy()

    def tcp_receiver_loop(self):
        while self.running:
            if not self.tcp_sock:
                time.sleep(0.2)
                continue
            buf = b""
            while self.connected:
                try:
                    data = self.tcp_sock.recv(4096)
                    if not data:
                        self.log("Disconnected from server", "error")
                        try:
                            self.gui_queue.put_nowait(self.cleanup_connection)
                        except:
                            pass
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        try:
                            msg = json.loads(line.decode())
                            try:
                                self.gui_queue.put_nowait(lambda m=msg: self.handle_control_message(m))
                            except Queue.Full:
                                pass  # Drop message if queue is full
                        except:
                            continue
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.connected:
                        self.log(f"TCP error: {e}", "error")
                        try:
                            self.gui_queue.put_nowait(self.cleanup_connection)
                        except:
                            pass
                    break

    def handle_control_message(self, msg):
        mtype = msg.get("type")
        
        if mtype == "chat":
            frm = msg.get("from")
            text = msg.get("message")
            self._update_chat(f"{frm}: {text}")
        
        elif mtype == "reaction":
            frm = msg.get("from")
            addr = msg.get("addr")
            emoji = msg.get("emoji")
            if addr and emoji:
                self.active_reactions[addr] = {"emoji": emoji, "timestamp": time.time()}
                self._update_chat(f"{frm}: {emoji}")
                self.schedule_canvas_update()
        
        elif mtype == "private_chat":
            frm = msg.get("from")
            text = msg.get("message")
            self._update_chat(f"[Private] {frm}: {text}")
        
        elif mtype == "private_chat_sent":
            to = msg.get("to")
            text = msg.get("message")
            self._update_chat(f"[Private] You to {to}: {text}")
        
        elif mtype == "user_list":
            users = msg.get("users", [])
            if self.username:
                my_info = next((u for u in users if u.get("name") == self.username), None)
                if my_info:
                    self.my_addr = my_info.get("addr")
                self.active_users_dict = [u for u in users if u.get("name") != self.username]
            else:
                self.active_users_dict = users
            
            for u in users:
                self.video_states.setdefault(u.get('addr'), True)
            
            self.active_users = [u.get("name") for u in self.active_users_dict]
            self.chat_target.configure(values=['Everyone'] + self.active_users)
            if self.chat_target.get() not in (['Everyone'] + self.active_users):
                self.chat_target.set("Everyone")
            self.update_users_display()
            self.schedule_canvas_update()
        
        elif mtype == "join":
            self.log(f"{msg.get('name')} joined", "system")
            self.video_states[msg.get('addr')] = True
        
        elif mtype == "leave":
            name = msg.get('name')
            addr = msg.get('addr')
            self.log(f"{name} left", "system")
            self.video_states.pop(addr, None)
            with self.frames_lock:
                self.frames_by_src.pop(addr, None)
            self.active_reactions.pop(addr, None)
            self.schedule_canvas_update()
        
        elif mtype == "video_start":
            self.video_states[msg.get("addr")] = True
            self.log(f"{msg.get('from')} started video", "system")
        
        elif mtype == "video_stop":
            self.video_states[msg.get("addr")] = False
            with self.frames_lock:
                self.frames_by_src.pop(msg.get("addr"), None)
            self.log(f"{msg.get('from')} stopped video", "system")
            self.schedule_canvas_update()
        
        elif mtype == "file_offer":
            frm = msg.get("from")
            fname = msg.get("filename")
            size = msg.get("size")
            self.show_file_offer(frm, fname, size)
        
        elif mtype == "error":
            self.log(f"{msg.get('message', '')}", "error")
            if "Username already taken" in msg.get('message', ''):
                self.cleanup_connection()
                messagebox.showerror("Error", "Username is already taken.")
        
        elif mtype == "present_start":
            self.log(f"{msg.get('from')} started presenting", "system")
        
        elif mtype == "present_stop":
            self.log(f"{msg.get('from')} stopped presenting", "system")
            self.screen_maximized = False
            if self.viewing_screen:
                self._cleanup_view_socket()

    def _update_chat(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.chat_text.insert("end", f"[{timestamp}] {text}\n")
        self.chat_text.see("end")

    def show_file_offer(self, from_user, filename, size):
        size_mb = size / (1024 * 1024)
        msg = f"{from_user} wants to share:\n\n{filename}\nSize: {size_mb:.2f} MB\n\nDownload?"
        if messagebox.askyesno("File Offer", msg):
            self.log(f"Downloading {filename}...", "system")
            threading.Thread(target=self.download_file, args=(filename,), daemon=True).start()
        else:
            self.log(f"Declined {filename}", "system")

    def download_file(self, filename):
        try:
            file_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_sock.settimeout(30.0)
            file_sock.connect((self.server_ip, FILE_TCP_PORT))
            request = json.dumps({"type": "file_download", "filename": filename}).encode()
            file_sock.sendall(request)
            
            info_data = file_sock.recv(4096)
            if info_data == b"ERROR":
                self.log(f"File not found: {filename}", "error")
                file_sock.close()
                return
            
            info = json.loads(info_data.decode().strip())
            size = info["size"]
            file_sock.sendall(b"READY")
            
            dest = os.path.join(DOWNLOADS_DIR, filename)
            remaining = size
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = file_sock.recv(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            
            file_sock.close()
            self.log(f"Downloaded: {filename}", "success")
        except Exception as e:
            self.log(f"Download error: {e}", "error")

    def send_chat(self):
        if not self.tcp_sock or not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        text = self.msg_entry.get().strip()
        if not text:
            return
        
        try:
            target = self.chat_target.get()
            if target == "Everyone":
                self.tcp_sock.sendall(pack_control({"type": "chat", "message": text}))
                self._update_chat(f"You: {text}")
            else:
                self.tcp_sock.sendall(pack_control({"type": "private_chat", "to": target, "message": text}))
            self.msg_entry.delete(0, "end")
        except Exception as e:
            self.log(f"Send failed: {e}", "error")
            try:
                self.gui_queue.put_nowait(self.cleanup_connection)
            except:
                pass

    def send_file(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        path = filedialog.askopenfilename(title="Select File")
        if not path:
            return
        threading.Thread(target=self._upload_file, args=(path,), daemon=True).start()

    def _upload_file(self, path):
        try:
            name = os.path.basename(path)
            size = os.path.getsize(path)
            self.log(f"Uploading {name}...", "system")
            
            file_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_sock.settimeout(60.0)
            file_sock.connect((self.server_ip, FILE_TCP_PORT))
            request = json.dumps({"type": "file_upload", "filename": name,
                                "size": size, "from": self.username}).encode()
            file_sock.sendall(request)
            
            ready = file_sock.recv(10)
            if ready != b"READY":
                raise Exception("Server not ready")
            
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    file_sock.sendall(chunk)
            
            done = file_sock.recv(10)
            file_sock.close()
            self.log(f"Upload complete: {name}", "success")
        except Exception as e:
            self.log(f"Upload failed: {e}", "error")

    def toggle_video(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        if not self.sending_video:
            self.sending_video = True
            self.video_btn.configure(fg_color=COLORS['active'],
                                    hover_color=COLORS['text_secondary'],
                                    text_color=COLORS['active_text'])
            threading.Thread(target=self.video_send_loop, daemon=True).start()
            self.log("Video started", "success")
            try:
                self.tcp_sock.sendall(pack_control({"type": "video_start"}))
            except:
                pass
        else:
            self.sending_video = False
            self.video_btn.configure(fg_color=COLORS['bg_tertiary'],
                                    hover_color=COLORS['hover'],
                                    text_color=COLORS['text_primary'])
            self.log("Video stopped", "system")
            try:
                self.tcp_sock.sendall(pack_control({"type": "video_stop"}))
            except:
                pass
            self.schedule_canvas_update()

    def video_send_loop(self):
        cap = None
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                self.log("Cannot open camera", "error")
                try:
                    self.gui_queue.put_nowait(self.toggle_video)
                except:
                    pass
                return
            
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, VIDEO_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, VIDEO_FPS)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            frame_id = 0
            frame_interval = 1.0 / VIDEO_FPS
            
            while self.sending_video and cap.isOpened():
                start = time.time()
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.01)
                    continue
                
                frame = cv2.resize(frame, (VIDEO_WIDTH, VIDEO_HEIGHT))
                ret2, jpg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                if not ret2:
                    continue
                
                data = jpg.tobytes()
                total = len(data)
                parts = (total + VIDEO_CHUNK - 1) // VIDEO_CHUNK
                
                for i in range(parts):
                    chunk = data[i*VIDEO_CHUNK:(i+1)*VIDEO_CHUNK]
                    header = struct.pack('!IHH', frame_id, parts, i)
                    try:
                        video_send_sock.sendto(header + chunk, (self.server_ip, SERVER_VIDEO_UDP_PORT))
                    except:
                        pass
                
                frame_id = (frame_id + 1) & 0xFFFFFFFF
                elapsed = time.time() - start
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        except Exception as e:
            self.log(f"Video send error: {e}", "error")
        finally:
            if cap:
                cap.release()
            self.sending_video = False

    # OPTIMIZED: Frame dropping and buffer management
    def video_receiver_loop(self):
        while self.running:
            try:
                pkt, addr = video_recv_sock.recvfrom(MAX_UDP_SIZE)
                if not pkt or len(pkt) < 12:
                    continue
                
                src_ip_packed = pkt[:4]
                src_ip = socket.inet_ntoa(src_ip_packed)
                
                if not self.video_states.get(src_ip, True):
                    continue
                
                frame_id, total_parts, part_idx = struct.unpack("!IHH", pkt[4:12])
                payload = pkt[12:]
                
                key = (src_ip, frame_id)
                
                # OPTIMIZED: Limit buffer size, drop old frames
                if key not in self.video_frames:
                    if len(self.video_frames) > FRAME_DROP_THRESHOLD:
                        # Drop oldest frames
                        oldest_keys = list(self.video_frames.keys())[:len(self.video_frames) - FRAME_DROP_THRESHOLD + 1]
                        for old_key in oldest_keys:
                            self.video_frames.pop(old_key, None)
                    
                    self.video_frames[key] = [None] * total_parts
                
                if part_idx >= total_parts or len(self.video_frames[key]) != total_parts:
                    self.video_frames.pop(key, None)
                    continue
                
                self.video_frames[key][part_idx] = payload
                
                if all(p is not None for p in self.video_frames[key]):
                    jpg = b"".join(self.video_frames.pop(key))
                    arr = np.frombuffer(jpg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil = Image.fromarray(frame)
                    
                    with self.frames_lock:
                        self.frames_by_src[src_ip] = pil
                    
                    self.schedule_canvas_update()
            
            except Exception as e:
                time.sleep(0.001)

    def redraw_canvas(self):
        self.gui_update_pending = False
        self.last_canvas_update = time.time()
        
        try:
            self.canvas.delete("all")
        except:
            return
        
        if self.screen_maximized and self.screen_frame:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw > 10 and ch > 10:
                try:
                    img = self.screen_frame.resize((cw, ch), Image.Resampling.LANCZOS)
                    tkimg = ImageTk.PhotoImage(img)
                    self.canvas.create_image(cw//2, ch//2, image=tkimg)
                    if not hasattr(self.canvas, '_imgs'):
                        self.canvas._imgs = []
                    self.canvas._imgs.clear()
                    self.canvas._imgs.append(tkimg)
                except:
                    pass
            return
        
        users_to_draw = []
        if self.my_addr:
            users_to_draw.append({
                "addr": self.my_addr,
                "name": f"{self.username} (You)",
                "is_self": True
            })
        users_to_draw.extend(self.active_users_dict)
        
        n = len(users_to_draw)
        if n == 0:
            try:
                self.canvas.create_text(self.canvas.winfo_width()//2, self.canvas.winfo_height()//2,
                                       text="No one in the meeting",
                                       fill=COLORS['text_muted'],
                                       font=('Segoe UI', 14))
            except:
                pass
            return
        
        try:
            cols = min(3, max(1, int(np.ceil(np.sqrt(n)))))
            rows = int(np.ceil(n / cols))
            cw = self.canvas.winfo_width() // cols
            ch = self.canvas.winfo_height() // rows
        except:
            return
        
        if cw <= 10 or ch <= 10:
            return
        
        if not hasattr(self.canvas, '_imgs'):
            self.canvas._imgs = []
        self.canvas._imgs.clear()
        
        current_time = time.time()
        idx = 0
        
        # Get frames with lock
        with self.frames_lock:
            frames_snapshot = self.frames_by_src.copy()
        
        for r in range(rows):
            for c in range(cols):
                if idx >= n:
                    break
                
                user = users_to_draw[idx]
                label_addr = user.get("addr")
                label_text = user.get("name")
                is_self = user.get("is_self", False)
                
                video_on = self.video_states.get(label_addr, True) if not is_self else self.sending_video
                pil = frames_snapshot.get(label_addr)
                
                x = c * cw
                y = r * ch
                
                self.canvas.create_rectangle(x + 1, y + 1, x + cw - 1, y + ch - 1,
                                            fill=COLORS['bg_secondary'],
                                            outline=COLORS['border'], width=1)
                
                if pil and video_on:
                    try:
                        img = pil.resize((cw - 4, ch - 4), Image.Resampling.BILINEAR)
                        tkimg = ImageTk.PhotoImage(img)
                        self.canvas.create_image(x + cw//2, y + ch//2, image=tkimg)
                        self.canvas._imgs.append(tkimg)
                    except:
                        pass
                else:
                    self.canvas.create_text(x + cw//2, y + ch//2,
                                          text=f"{label_text}\n(Video Off)",
                                          fill=COLORS['text_muted'],
                                          font=('Segoe UI', 11),
                                          justify=tk.CENTER)
                
                badge_y_pos = y + ch - 30
                self.canvas.create_rectangle(x + 10, badge_y_pos, x + 15 + len(label_text) * 7, badge_y_pos + 20,
                                            fill=COLORS['bg_tertiary'], outline="", tags="badge")
                self.canvas.create_text(x + 12, badge_y_pos + 10, text=label_text,
                                       fill=COLORS['text_primary'],
                                       anchor=tk.W, font=('Segoe UI', 9))
                
                reaction = self.active_reactions.get(label_addr)
                if reaction:
                    age = current_time - reaction["timestamp"]
                    if age < REACTION_DURATION_SECONDS:
                        try:
                            progress = age / REACTION_DURATION_SECONDS
                            size_progress = math.sin(progress * math.pi)
                            current_size = int(10 + 62 * size_progress)
                            self.canvas.create_text(
                                x + cw // 2, y + ch // 2 + 20,
                                text=reaction["emoji"],
                                font=('Segoe UI Emoji', current_size, 'bold'),
                                fill='#FFFFFF',
                                anchor=tk.CENTER
                            )
                        except Exception as e:
                            print(f"Error drawing reaction: {e}")
                
                idx += 1

    def toggle_audio(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        if not PYAUDIO_AVAILABLE:
            messagebox.showerror("Audio library missing", "pyaudio not installed")
            return
        
        if not self.sending_audio:
            self.sending_audio = True
            self.audio_btn.configure(fg_color=COLORS['active'],
                                    hover_color=COLORS['text_secondary'],
                                    text_color=COLORS['active_text'])
            threading.Thread(target=self.audio_capture_loop, daemon=True).start()
            self.log("Mic ON", "success")
        else:
            self.sending_audio = False
            self.audio_btn.configure(fg_color=COLORS['bg_tertiary'],
                                    hover_color=COLORS['hover'],
                                    text_color=COLORS['text_primary'])
            self.stop_audio_capture()
            self.log("Mic OFF", "system")

    def audio_capture_loop(self):
        try:
            self.audio_capture_stream = self.pa.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                                                     rate=AUDIO_RATE, input=True,
                                                     frames_per_buffer=AUDIO_INPUT_CHUNK,
                                                     stream_callback=None)
            while self.sending_audio:
                try:
                    data = self.audio_capture_stream.read(AUDIO_INPUT_CHUNK, exception_on_overflow=False)
                    if len(data) > 0 and self.connected:
                        audio_send_sock.sendto(data, (self.server_ip, SERVER_AUDIO_UDP_PORT))
                except IOError as e:
                    if e.errno == pyaudio.paInputOverflowed:
                        pass
                    else:
                        raise
                except:
                    pass
        except Exception as e:
            self.log(f"Audio capture error: {str(e)}", "error")
        finally:
            try:
                if self.audio_capture_stream:
                    self.audio_capture_stream.stop_stream()
                    self.audio_capture_stream.close()
                    self.audio_capture_stream = None
            except:
                pass
            self.sending_audio = False
            try:
                self.gui_queue.put_nowait(lambda: self.audio_btn.configure(fg_color=COLORS['bg_tertiary']))
            except:
                pass

    def stop_audio_capture(self):
        self.sending_audio = False

    def start_audio_playback(self):
        try:
            if self.audio_play_stream:
                self.audio_play_stream.stop_stream()
                self.audio_play_stream.close()
            self.audio_play_stream = self.pa.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
                                                 rate=AUDIO_RATE, output=True,
                                                 frames_per_buffer=AUDIO_CHUNK,
                                                 stream_callback=None)
            self.log("Audio playback active", "success")
        except Exception as e:
            self.log(f"Audio playback error: {str(e)}", "error")

    def audio_receiver_loop(self):
        while self.running:
            try:
                if self.connected:
                    try:
                        audio_recv_sock.settimeout(0.05)
                        pkt, addr = audio_recv_sock.recvfrom(8192)
                        if pkt and len(pkt) >= 2:
                            try:
                                self.audio_buffer.put(pkt, block=False)
                            except Queue.Full:
                                try:
                                    self.audio_buffer.get_nowait()
                                    self.audio_buffer.put(pkt, block=False)
                                except:
                                    pass
                    except socket.timeout:
                        pass
                else:
                    time.sleep(0.1)
            except:
                time.sleep(0.01)

    def audio_playback_loop(self):
        while self.running:
            try:
                if self.audio_play_stream and self.connected:
                    try:
                        pkt = self.audio_buffer.get(timeout=0.100)
                        self.audio_play_stream.write(pkt, exception_on_underflow=False)
                    except Queue.Empty:
                        pass
                    except IOError as e:
                        if e.errno == pyaudio.paOutputUnderflowed:
                            pass
                else:
                    time.sleep(0.1)
            except Exception as e:
                time.sleep(0.01)

    def _cleanup_share_socket(self):
        with self.screen_share_lock:
            self.sharing_screen = False
            if self.sharing_screen_sock:
                try:
                    self.sharing_screen_sock.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                try:
                    self.sharing_screen_sock.close()
                except:
                    pass
                self.sharing_screen_sock = None
            self.log("Share socket closed", "system")
            try:
                self.gui_queue.put_nowait(lambda: self.screen_btn.configure(fg_color=COLORS['bg_tertiary'],
                                                                     hover_color=COLORS['hover'],
                                                                     text_color=COLORS['text_primary']))
            except:
                pass

    def _cleanup_view_socket(self):
        with self.screen_share_lock:
            self.viewing_screen = False
            self.screen_maximized = False
            self.screen_frame = None
            if self.viewing_screen_sock:
                try:
                    self.viewing_screen_sock.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                try:
                    self.viewing_screen_sock.close()
                except:
                    pass
                self.viewing_screen_sock = None
            self.log("View socket closed", "system")
            try:
                self.gui_queue.put_nowait(lambda: self.view_screen_btn.configure(fg_color=COLORS['bg_tertiary'],
                                                                          hover_color=COLORS['hover'],
                                                                          text_color=COLORS['text_primary']))
            except:
                pass
            self.schedule_canvas_update()

    def toggle_screen_share(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        if not MSS_AVAILABLE:
            messagebox.showerror("MSS not available", "Install mss library")
            return
        
        with self.screen_share_lock:
            is_sharing = self.sharing_screen
        
        if not is_sharing:
            self.log("Starting screen share...", "system")
            threading.Thread(target=self._start_screen_share, daemon=True).start()
        else:
            self.log("Stopping screen share...", "system")
            self._stop_screen_share()

    def _stop_screen_share(self):
        with self.screen_share_lock:
            self.sharing_screen = False
        try:
            if self.tcp_sock:
                self.tcp_sock.sendall(pack_control({"type": "present_stop"}))
        except:
            pass
        threading.Thread(target=self._cleanup_share_socket, daemon=True).start()

    def _start_screen_share(self):
        self._cleanup_share_socket()
        time.sleep(0.5)
        
        try:
            self.sharing_screen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sharing_screen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sharing_screen_sock.settimeout(15.0)
            self.log("Connecting to screen share server...", "system")
            self.sharing_screen_sock.connect((self.server_ip, SCREEN_TCP_PORT))
            
            if not write_msg(self.sharing_screen_sock, {"role": "presenter"}):
                raise ConnectionError("Failed to send presenter role")
            
            response = read_msg(self.sharing_screen_sock)
            if response is None:
                raise ConnectionError("Server did not respond - timeout")
            
            if response.get("status") != "ok":
                reason = response.get("reason", "Server rejected request")
                try:
                    self.gui_queue.put_nowait(lambda r=reason: messagebox.showerror("Screen Share", f"Rejected: {r}"))
                except:
                    pass
                self._cleanup_share_socket()
                return
            
            with self.screen_share_lock:
                self.sharing_screen = True
                self.sharing_screen_sock.settimeout(None)
            
            try:
                self.gui_queue.put_nowait(lambda: self.screen_btn.configure(fg_color=COLORS['active'],
                                                                     hover_color=COLORS['text_secondary'],
                                                                     text_color=COLORS['active_text']))
            except:
                pass
            
            self.log("Screen sharing started successfully!", "success")
            
            try:
                if self.tcp_sock:
                    self.tcp_sock.sendall(pack_control({"type": "present_start"}))
            except:
                pass
            
            self.screen_capture_loop()
        
        except Exception as e:
            self.log(f"Screen share error: {e}", "error")
            try:
                self.gui_queue.put_nowait(lambda: messagebox.showerror("Screen Share",
                                                                f"Connection failed: {e}\n\nPlease try again."))
            except:
                pass
            self._cleanup_share_socket()

    def screen_capture_loop(self):
        frame_interval = 1.0 / SCREEN_FPS
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                while True:
                    with self.screen_share_lock:
                        if not self.sharing_screen:
                            break
                    
                    start = time.time()
                    try:
                        screenshot = sct.grab(monitor)
                        img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)
                        img = img.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format='JPEG', quality=SCREEN_QUALITY, optimize=True)
                        frame_data = buf.getvalue()
                        
                        if not write_msg(self.sharing_screen_sock, {"type": "frame", "data": frame_data.hex(),
                                                                     "width": SCREEN_WIDTH, "height": SCREEN_HEIGHT}):
                            self.log("Screen send failed - Connection lost", "error")
                            break
                    except Exception as e:
                        self.log(f"Screen frame error: {e}", "error")
                        break
                    
                    elapsed = time.time() - start
                    sleep_time = max(0, frame_interval - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
        
        except Exception as e:
            self.log(f"Screen capture error: {e}", "error")
        finally:
            self._stop_screen_share()
            self.log("Screen capture loop ended", "system")

    def toggle_screen_view(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        with self.screen_share_lock:
            is_viewing = self.viewing_screen
        
        if not is_viewing:
            self.log("Starting screen view...", "system")
            threading.Thread(target=self._start_screen_view, daemon=True).start()
        else:
            self.log("Stopping screen view...", "system")
            self._cleanup_view_socket()

    def _start_screen_view(self):
        self._cleanup_view_socket()
        time.sleep(0.5)
        
        try:
            self.viewing_screen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.viewing_screen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.viewing_screen_sock.settimeout(15.0)
            self.log("Connecting to view screen...", "system")
            self.viewing_screen_sock.connect((self.server_ip, SCREEN_TCP_PORT))
            
            if not write_msg(self.viewing_screen_sock, {"role": "viewer"}):
                raise ConnectionError("Failed to send role")
            
            response = read_msg(self.viewing_screen_sock)
            if response is None:
                raise ConnectionError("No response from server - Connection timeout")
            
            if response.get("status") != "ok":
                reason = response.get("reason", "Unknown error")
                try:
                    self.gui_queue.put_nowait(lambda r=reason: messagebox.showerror("View Screen", r))
                except:
                    pass
                self._cleanup_view_socket()
                return
            
            with self.screen_share_lock:
                self.viewing_screen = True
                self.screen_maximized = True
                self.viewing_screen_sock.settimeout(None)
            
            try:
                self.gui_queue.put_nowait(lambda: self.view_screen_btn.configure(fg_color=COLORS['active'],
                                                                          hover_color=COLORS['text_secondary'],
                                                                          text_color=COLORS['active_text']))
            except:
                pass
            
            self.log("Viewing screen - Connected successfully", "success")
            
            self.screen_receive_loop()
        
        except Exception as e:
            self.log(f"View failed: {e}", "error")
            try:
                self.gui_queue.put_nowait(lambda: messagebox.showerror("View Screen", f"Failed to connect: {str(e)}"))
            except:
                pass
            self._cleanup_view_socket()

    def screen_receive_loop(self):
        try:
            while True:
                with self.screen_share_lock:
                    if not self.viewing_screen:
                        break
                
                msg = read_msg(self.viewing_screen_sock)
                if not msg:
                    self.log("Screen view disconnected", "system")
                    break
                
                if msg.get("type") == "frame":
                    frame_data = bytes.fromhex(msg["data"])
                    img = Image.open(io.BytesIO(frame_data))
                    self.screen_frame = img
                    self.schedule_canvas_update()
                
                elif msg.get("type") == "present_stop":
                    self.log("Presenter stopped", "system")
                    break
        
        except Exception as e:
            if self.viewing_screen:
                self.log(f"Screen receive error: {e}", "error")
        finally:
            self._cleanup_view_socket()

    def log(self, text, msg_type="default"):
        try:
            self.gui_queue.put_nowait(lambda: self._update_log(text, msg_type))
        except:
            pass

    def _update_log(self, text, msg_type="default"):
        try:
            timestamp = time.strftime("%H:%M:%S")
            self.log_textbox.insert("end", f"[{timestamp}] {text}\n")
            self.log_textbox.see("end")
        except:
            pass

if __name__ == "__main__":
    root = ctk.CTk()
    app = ModernConferenceClient(root)
    root.mainloop()

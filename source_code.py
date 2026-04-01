""" -----------------------------------------------------Server.py-----------------------------------------------------------------
import socket
import threading
import json
import struct
import os
import sys
import time
import logging
from collections import defaultdict, deque

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========= Configuration =========
TCP_PORT = 9000
VIDEO_UDP_PORT = 10000
AUDIO_UDP_PORT = 11000
SCREEN_TCP_PORT = 9001
FILE_TCP_PORT = 9002
MAX_UDP_SIZE = 65507
VIDEO_CHUNK_DATA = 1100
AUDIO_BUFFER_SIZE = 10
AUDIO_CHUNK_DURATION = 0.016  
SPEAKER_BROADCAST_INTERVAL = 1.0 # Min time between speaker active messages

# Use '0.0.0.0' to listen on all available network interfaces
#SERVER_HOST = '172.17.249.173'
def get_local_ip():
    s = None
    try:
        # Create a dummy UDP socket and connect to a public IP
        # This doesn't send data, just finds the right outgoing interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception as e:
        logger.warning(f"Could not auto-detect IP: {e}. Falling back to '0.0.0.0'")
        ip = '0.0.0.0' # Fallback
    finally:
        if s:
            s.close()
    return ip

SERVER_HOST = get_local_ip()

# ===== Global State =====
clients_lock = threading.Lock()
clients = {}
clients_by_name = {}
udp_video_targets = set()
udp_audio_targets = {}
audio_queues = defaultdict(lambda: deque(maxlen=AUDIO_BUFFER_SIZE))
screen_presenter = None
screen_viewers = {}
# Use a Re-entrant Lock for screen sharing to prevent deadlocks
screen_lock = threading.RLock() 

os.makedirs("server_files", exist_ok=True)

# ===== Framing Utilities =====
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
        logger.warning("read_msg: Socket read timed out")
        return None
    except Exception as e:
        logger.error(f"read_msg: Error: {e}")
        return None

# ===== TCP Helpers =====
def send_json(conn, obj):
    try:
        raw = (json.dumps(obj) + "\n").encode()
        conn.sendall(raw)
    except:
        pass

def broadcast_json(obj, exclude_conn=None):
    with clients_lock:
        for c in list(clients.keys()):
            try:
                if c is exclude_conn:
                    continue
                send_json(c, obj)
            except:
                cleanup_client(c)

def get_user_list():
    with clients_lock:
        return [{"name": info["name"], "addr": f"{info['addr'][0]}"} for info in clients.values()]

def cleanup_client(conn, name_from_info=None):
    info = None
    name = name_from_info
    client_ip = None
    
    with clients_lock:
        info = clients.pop(conn, None)
        if info:
            name = info.get("name", "unknown")
            client_ip = info.get("addr", ["unknown"])[0]
            clients_by_name.pop(name, None)
            
            try:
                if info.get("video_port"):
                    udp_video_targets.discard((info["addr"][0], info["video_port"]))
                if info.get("audio_port"):
                    udp_audio_targets.pop((info["addr"][0], info["audio_port"]), None)
            except:
                pass
    
    if info:
        logger.info(f"[LEFT] {name} @ {info.get('addr')}")
        user_list = get_user_list()
        broadcast_json({"type": "user_list", "users": user_list})
        broadcast_json({"type": "leave", "name": name, "addr": client_ip})
    elif name_from_info:
        logger.info(f"[LEFT] {name_from_info} (redundant cleanup)")
    
    try:
        conn.close()
    except:
        pass

# ===== TCP Control Handler =====
def handle_control(conn, addr):
    name = None
    try:
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                break
            
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue
                
                try:
                    msg = json.loads(line.decode())
                except Exception as e:
                    logger.error(f"Bad JSON from {addr}: {e}")
                    continue
                
                mtype = msg.get("type")
                
                if mtype == "hello":
                    name = msg.get("name", "anonymous")
                    vport = int(msg.get("video_port", 0) or 0)
                    aport = int(msg.get("audio_port", 0) or 0)
                    
                    with clients_lock:
                        if name in clients_by_name:
                            send_json(conn, {"type": "error", "message": "Username already taken"})
                            name = None
                            break
                        
                        clients[conn] = {
                            "name": name,
                            "addr": addr,
                            "video_port": vport,
                            "audio_port": aport,
                            "last_seen": time.time()
                        }
                        clients_by_name[name] = conn
                        
                        if vport:
                            udp_video_targets.add((addr[0], vport))
                        if aport:
                            udp_audio_targets[(addr[0], aport)] = (conn, name)
                    
                    logger.info(f"[JOIN] {name} @ {addr} vport={vport} aport={aport}")
                    user_list = get_user_list()
                    # Send list to new user
                    send_json(conn, {"type": "user_list", "users": user_list})
                    # Tell everyone else about new user
                    broadcast_json({"type": "join", "name": name, "addr": addr[0]}, exclude_conn=conn)
                    # Send updated list to everyone else
                    broadcast_json({"type": "user_list", "users": user_list}, exclude_conn=conn)
                
                elif not name:
                    break
                
                elif mtype == "chat":
                    broadcast_json({"type": "chat", "from": name, "message": msg.get("message", "")})
                
                elif mtype == "reaction":
                    # Re-broadcast reaction with sender's address
                    broadcast_json({
                        "type": "reaction", 
                        "from": name, 
                        "addr": addr[0],
                        "emoji": msg.get("emoji")
                    }, exclude_conn=None) # Send to everyone, including sender
                
                elif mtype == "hand_raise":
                    # Re-broadcast hand raise state with sender's address
                    broadcast_json({
                        "type": "hand_raise",
                        "from": name,
                        "addr": addr[0],
                        "state": msg.get("state")
                    }, exclude_conn=None) # Send to everyone
                    
                elif mtype == "video_start":
                    broadcast_json({"type": "video_start", "from": name, "addr": addr[0]}, exclude_conn=conn)

                elif mtype == "video_stop":
                    broadcast_json({"type": "video_stop", "from": name, "addr": addr[0]}, exclude_conn=conn)

                elif mtype == "private_chat":
                    target_name = msg.get("to")
                    message = msg.get("message", "")
                    target_conn = None
                    
                    with clients_lock:
                        target_conn = clients_by_name.get(target_name)
                    
                    if target_conn:
                        send_json(target_conn, {
                            "type": "private_chat",
                            "from": name,
                            "message": message
                        })
                        send_json(conn, {
                            "type": "private_chat_sent",
                            "to": target_name,
                            "message": message
                        })
                    else:
                        send_json(conn, {
                            "type": "error",
                            "message": f"User {target_name} not found"
                        })
                
                elif mtype == "present_start":
                    broadcast_json({"type": "present_start", "from": name}, exclude_conn=conn)
                
                elif mtype == "present_stop":
                    broadcast_json({"type": "present_stop", "from": name}, exclude_conn=conn)
                
                elif mtype == "bye":
                    break
    
    except Exception as e:
        logger.debug(f"Control handler exception: {e}")
    finally:
        cleanup_client(conn, name)

# ===== File Transfer Server =====
def file_transfer_server():
    logger.info(f"[FILE] Server listening on TCP {FILE_TCP_PORT}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((SERVER_HOST, FILE_TCP_PORT))
    s.listen(10)
    
    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_file_transfer, args=(conn, addr), daemon=True).start()
    except:
        pass
    finally:
        s.close()

def handle_file_transfer(conn, addr):
    try:
        data = conn.recv(4096)
        if not data:
            return
        
        msg = json.loads(data.decode())
        mtype = msg.get("type")
        
        if mtype == "file_upload":
            filename = msg.get("filename")
            size = msg.get("size")
            sender_name = msg.get("from")
            safe = os.path.basename(filename)
            dest = os.path.join("server_files", safe)
            
            conn.sendall(b"READY")
            remaining = size
            
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = conn.recv(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            
            logger.info(f"[FILE] Received {safe} ({size} bytes) from {sender_name}")
            broadcast_json({
                "type": "file_offer",
                "from": sender_name,
                "filename": safe,
                "size": size
            })
            conn.sendall(b"DONE")
        
        elif mtype == "file_download":
            filename = msg.get("filename")
            path = os.path.join("server_files", os.path.basename(filename))
            
            if not os.path.exists(path):
                conn.sendall(b"ERROR")
                return
            
            size = os.path.getsize(path)
            info = json.dumps({"size": size}).encode()
            conn.sendall(info + b"\n")
            conn.recv(10)
            
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    conn.sendall(chunk)
            
            logger.info(f"[FILE] Sent {filename} to {addr}")
    
    except Exception as e:
        logger.error(f"[FILE] Transfer error: {e}")
    finally:
        try:
            conn.close()
        except:
            pass

# ===== Video Forwarder =====
video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_sock.bind((SERVER_HOST, VIDEO_UDP_PORT))

def video_forwarder():
    logger.info(f"[VIDEO] Forwarder listening on UDP {VIDEO_UDP_PORT}")
    while True:
        try:
            data, addr = video_sock.recvfrom(MAX_UDP_SIZE)
            if len(data) < 8:
                continue
            
            try:
                # Prepend the sender's IP address
                src_ip_packed = socket.inet_aton(addr[0])
            except:
                src_ip_packed = b'\x00\x00\x00\x00'
            
            outpkt = src_ip_packed + data
            
            with clients_lock:
                targets = list(udp_video_targets)
            
            for tgt_addr, tgt_port in targets:
                # Don't send video back to the sender
                if tgt_addr == addr[0]:
                    continue
                try:
                    video_sock.sendto(outpkt, (tgt_addr, tgt_port))
                except:
                    pass
        
        except Exception as e:
            logger.error(f"[VIDEO] Forwarder error: {e}")
            pass

# ===== Audio Receiver & Mixer =====
audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_sock.bind((SERVER_HOST, AUDIO_UDP_PORT))
last_speaker_addr = None
last_speaker_broadcast_time = 0

def audio_receiver():
    global last_speaker_addr, last_speaker_broadcast_time
    logger.info(f"[AUDIO] Receiver listening on UDP {AUDIO_UDP_PORT}")
    
    while True:
        try:
            data, addr = audio_sock.recvfrom(8192)
            if data:
                audio_queues[addr].append(data)
                
                # --- Active Speaker Logic ---
                now = time.time()
                if (addr != last_speaker_addr or 
                    now - last_speaker_broadcast_time > SPEAKER_BROADCAST_INTERVAL):
                    
                    last_speaker_addr = addr
                    last_speaker_broadcast_time = now
                    # Send this to the main TCP handler thread to broadcast
                    broadcast_json({"type": "speaker_active", "addr": addr[0]})
                # ----------------------------

        except:
            pass

def audio_mixer():
    import numpy as np
    logger.info("[AUDIO] Mixer started - High-Precision Ticker & PLC enabled")
    last_good_audio = {}
    
    while True:
        tick_start = time.time()
        
        try:
            frames = []
            sources = []
            
            with clients_lock:
                current_audio_targets = set(udp_audio_targets.keys())
                known_ips = {ip for (ip, port) in current_audio_targets}
            
            # 1. Gather audio frames, using last good packet (PLC) if queue is empty
            for addr, q in list(audio_queues.items()):
                if addr[0] not in known_ips:
                    last_good_audio.pop(addr, None)
                    audio_queues.pop(addr, None)
                    continue
                
                if len(q) > 0:
                    try:
                        pkt = q.popleft()
                        frames.append(pkt)
                        sources.append(addr)
                        last_good_audio[addr] = pkt # Store as last good packet
                    except IndexError:
                        pass
                elif addr in last_good_audio:
                    frames.append(last_good_audio[addr]) # Use last good packet
                    sources.append(addr)
            
            # 2. Clean up PLC buffer for disconnected users
            for addr in list(last_good_audio.keys()):
                if addr[0] not in known_ips:
                    last_good_audio.pop(addr, None)
            
            if not frames:
                pass
            else:
                # 3. Decode frames
                arrays = [np.frombuffer(f, dtype=np.int16) for f in frames if len(f) > 0 and len(f) % 2 == 0]
                
                if arrays:
                    minlen = min(a.shape[0] for a in arrays)
                    arrays = [a[:minlen] for a in arrays]
                    
                    with clients_lock:
                        targets = list(udp_audio_targets.items())
                    
                    # 4. Mix and send to each target
                    for tgt_addr_tuple, (tgt_conn, tgt_name) in targets:
                        tgt_addr = (tgt_addr_tuple[0], tgt_addr_tuple[1])
                        
                        # Filter out the target's own audio
                        tgt_arrays = []
                        for i, src_addr in enumerate(sources):
                            if src_addr[0] != tgt_addr_tuple[0]:
                                tgt_arrays.append(arrays[i])
                        
                        if tgt_arrays:
                            # --- FIX: Use np.sum for mixing, not np.mean ---
                            stacked = np.vstack(tgt_arrays)
                            mixed_float = np.sum(stacked.astype(np.float32), axis=0)
                            # Clip to prevent overflow
                            mixed = np.clip(mixed_float, -32768, 32767).astype(np.int16)
                            # -----------------------------------------------
                            pkt = mixed.tobytes()
                            
                            try:
                                audio_sock.sendto(pkt, tgt_addr)
                            except:
                                pass
        
        except Exception as e:
            logger.error(f"[AUDIO] Mixer error: {e}")
        
        # 5. High-precision ticker loop
        tick_end = time.time()
        elapsed = tick_end - tick_start
        sleep_time = AUDIO_CHUNK_DURATION - elapsed
        
        if sleep_time > 0.002:
            time.sleep(sleep_time - 0.001)
        
        while time.time() < tick_start + AUDIO_CHUNK_DURATION:
            pass

# ===== Screen Sharing Relay =====
def screen_relay_server():
    logger.info(f"[SCREEN] Relay listening on TCP {SCREEN_TCP_PORT}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((SERVER_HOST, SCREEN_TCP_PORT))
    s.listen(50)
    
    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_screen_connection, args=(conn, addr), daemon=True).start()
    except:
        pass
    finally:
        s.close()

def handle_screen_connection(conn, addr):
    global screen_presenter
    role = None
    
    try:
        conn.settimeout(10.0)
        role_msg = read_msg(conn)
        if not role_msg:
            return
        
        role = role_msg.get("role")
        
        if role == "presenter":
            with screen_lock:
                if screen_presenter is None:
                    screen_presenter = {"socket": conn, "addr": addr}
                    write_msg(conn, {"status": "ok"})
                    logger.info(f"[SCREEN] Presenter connected: {addr}")
                else:
                    write_msg(conn, {"status": "denied", "reason": "Presenter already active"})
                    conn.close()
                    return
            
            try:
                conn.settimeout(None) # Remove timeout for streaming
                while True:
                    frame = read_msg(conn)
                    if not frame:
                        break
                    broadcast_screen_frame(frame)
            except:
                pass
            finally:
                with screen_lock:
                    if screen_presenter and screen_presenter["socket"] == conn:
                        screen_presenter = None
                        logger.info(f"[SCREEN] Presenter {addr} disconnected. Releasing lock.")
                        broadcast_screen_frame({"type": "present_stop"})
        
        elif role == "viewer":
            with screen_lock:
                screen_viewers[conn] = addr
                if screen_presenter:
                    write_msg(conn, {"status": "ok", "reason": "Presenter active"})
                else:
                    write_msg(conn, {"status": "ok", "reason": "No presenter"})
            
            logger.info(f"[SCREEN] Viewer connected: {addr}")
            
            try:
                conn.settimeout(None)
                while True:
                    # Keep connection alive, wait for data (which will be nothing)
                    data = conn.recv(1)
                    if not data:
                        break # Disconnected
            except:
                pass
            finally:
                with screen_lock:
                    screen_viewers.pop(conn, None)
                logger.info(f"[SCREEN] Viewer disconnected: {addr}")
    
    except Exception as e:
        logger.error(f"[SCREEN] Connection error: {e}")
    finally:
        if role == "presenter":
            with screen_lock:
                if screen_presenter and screen_presenter["socket"] == conn:
                    screen_presenter = None
                    logger.info(f"[SCREEN] Presenter {addr} disconnected in final block. Releasing lock.")
                    broadcast_screen_frame({"type": "present_stop"})
        elif role == "viewer":
            with screen_lock:
                screen_viewers.pop(conn, None)
        
        try:
            conn.close()
        except:
            pass

def broadcast_screen_frame(frame_data):
    with screen_lock:
        dead_viewers = []
        for viewer_sock, viewer_addr in screen_viewers.items():
            if not write_msg(viewer_sock, frame_data):
                dead_viewers.append(viewer_sock)
        
        for dead in dead_viewers:
            try:
                dead.close()
            except:
                pass
            screen_viewers.pop(dead, None)

# ===== Main Server =====
def start_server():
    threading.Thread(target=video_forwarder, daemon=True).start()
    threading.Thread(target=audio_receiver, daemon=True).start()
    threading.Thread(target=audio_mixer, daemon=True).start()
    threading.Thread(target=screen_relay_server, daemon=True).start()
    threading.Thread(target=file_transfer_server, daemon=True).start()
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((SERVER_HOST, TCP_PORT))
    s.listen(50)
    logger.info(f"[TCP] Control server listening on {SERVER_HOST}:{TCP_PORT}")
    
    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_control, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Shutting down server")
    finally:
        s.close()

if __name__ == "__main__":
    start_server()

 -----------------------------------------------------Client.py-----------------------------------------------------------------

import socket
import threading
import json
import struct
import time
import os
import io
import random
import math # Added for animation
import base64 # FIX 1: Import base64
import hashlib # FIX 2: Import for deterministic emoji positions
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image, ImageTk, ImageDraw
import cv2
import numpy as np
import queue as Queue
# Removed: uuid, datetime (no longer needed for feedback)

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

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False
    print("--- Mediapipe not installed. Virtual Background feature disabled. ---")
    print("--- Run `pip install mediapipe` to enable. ---")


# ====== Configuration ======
SERVER_TCP_PORT = 9000
VIDEO_UDP_PORT = 10000
AUDIO_UDP_PORT = 11000
SCREEN_TCP_PORT = 9001
FILE_TCP_PORT = 9002
# Removed: FEEDBACK_TCP_PORT
LOCAL_VIDEO_LISTEN_PORT = 10001
LOCAL_AUDIO_LISTEN_PORT = 11001

# FIX v7: Reverted to stable-performance settings
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
VIDEO_FPS = 15 # Reverted from 20
VIDEO_CHUNK = 1100
JPEG_QUALITY = 80 # Reverted from 85

# FIX v7: Reverted to 16kHz audio
AUDIO_RATE = 16000 # Reverted from 24000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None
AUDIO_CHUNK = 256 # Reverted from 384
AUDIO_INPUT_CHUNK = 256 # Reverted from 384
MAX_UDP_SIZE = 65507

# --- CHANGE 1: Shortened duration for new "pop" animation ---
REACTION_DURATION_SECONDS = 1.5 # How long an emoji animation lasts

# Removed: BACK_ARROW_ICON_B64

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
        # Set a reasonable timeout for read operations
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

# ====== Networking Sockets (Global for UDP) ======
# These are global because they are bound to ports for the app's lifetime
video_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_recv_sock.bind(("0.0.0.0", LOCAL_VIDEO_LISTEN_PORT))

audio_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_recv_sock.bind(("0.0.0.0", LOCAL_AUDIO_LISTEN_PORT))

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "ConferenceFiles")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# ====== Modern Conference Client ======
class ModernConferenceClient:
    def __init__(self, master):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.master = master
        self.master.title("HELIX")
        
        # --- ADD THIS BLOCK TO SET THE WINDOW ICON ---
        # --- (You must convert your JPG to an 'icon.ico' file for this) ---
        try:
            # Look for 'icon.ico' in the same folder as the script
            # NOTE: If 'icon.ico' is missing or corrupted, this will fail silently on some systems.
            self.master.iconbitmap("icon.ico") 
        except Exception as e:
            # Added a print statement here to make debugging easier for the user
            print(f"Error loading window icon 'icon.ico': {e}")
            print("Please ensure 'icon.ico' (converted from your logo) is in the same folder as the script.")
        # --- END OF NEW BLOCK ---
        
        self.master.geometry("1400x900")
        
        self.username = None
        self.server_ip = None
        # Removed: self.client_uuid
        self.connected = False
        self.sending_video = False
        self.sending_audio = False
        
        # Screen share state
        self.screen_share_lock = threading.Lock()
        self.sharing_screen = False
        self.viewing_screen = False
        self.screen_maximized = False
        
        # Sockets as instance variables
        self.tcp_sock = None
        self.sharing_screen_sock = None
        self.viewing_screen_sock = None
        
        # State tracking
        self.frames_by_src = {}
        self.video_frames = {}
        self.screen_frame = None
        self.current_presenter_addr = None # FIX 2: Track current presenter
        self.active_users = []
        self.active_users_dict = {}
        self.video_states = {} # New: track who has video on/off
        self.my_addr = None # New: track our own IP as server sees it
        self.active_reactions = [] # New: List for floating emoji animations
        
        # FIX 4: Active Speaker State
        self.active_speaker_addr = None
        self.active_speaker_timer = None
        
        self.gui_queue = Queue.Queue()
        
        # --- CHANGE 1: ADDED THIS LIST FOR AVATAR COLORS ---
        self.avatar_colors = [
            "#e53935", "#d81b60", "#8e24aa", "#5e35b1", "#3949ab",
            "#1e88e5", "#039be5", "#00acc1", "#00897b", "#43a047",
            "#7cb342", "#c0ca33", "#fdd835", "#ffb300", "#fb8c00",
            "#f4511e", "#6d4c41", "#757575", "#546e7a"
        ]
        # --- END OF CHANGE 1 ---
        
        self.pa = pyaudio.PyAudio() if PYAUDIO_AVAILABLE else None
        self.audio_play_stream = None
        self.audio_capture_stream = None
        self.audio_buffer = Queue.Queue(maxsize=30)
        self.running = True
        
        # FIX v7: Custom VBG state
        self.vbg_state = None # None, "image"
        self.vbg_image_raw = None # Stores PIL image
        self.vbg_image_cv = None  # Stores pre-resized OpenCV (Numpy) image
        self.mp_segmentation = None
        self.segmentation = None
        
        # --- FIX: ADDED SCREEN CONFIG TO CLASS (to fix warnings) ---
        self.SCREEN_WIDTH = 1280
        self.SCREEN_HEIGHT = 720
        self.SCREEN_FPS = 10
        self.SCREEN_QUALITY = 50
        # --- END OF FIX ---
        
        # FIX v7.3: Correctly handle global scope
        global MEDIAPIPE_AVAILABLE
        if MEDIAPIPE_AVAILABLE:
            try:
                self.mp_segmentation = mp.solutions.selfie_segmentation
                self.segmentation = self.mp_segmentation.SelfieSegmentation(model_selection=0) # 0 = General, 1 = Landscape
            except Exception as e:
                print(f"Failed to initialize Mediapipe: {e}")
                MEDIAPIPE_AVAILABLE = False # Disable if init fails
        
        # FIX v8.0: Remove old canvas update timing logic
        # self.last_canvas_update = 0
        # self.canvas_update_interval = 1.0 / 30
        
        self.dark_mode = True
        self.sidebar_visible = False

        # Removed: self._load_icons()
        self._build_ui()
        
        # Start core loops
        threading.Thread(target=self.tcp_receiver_loop, daemon=True).start()
        threading.Thread(target=self.video_receiver_loop, daemon=True).start()
        threading.Thread(target=self.audio_receiver_loop, daemon=True).start()
        threading.Thread(target=self.audio_playback_loop, daemon=True).start()
        
        self.process_gui_queue()
        self.reaction_updater_loop() # Start the emoji cleanup loop
        
        # FIX v8.0: Start the new canvas update loop
        self.canvas_updater_loop()
        
        self.log(f"💾 Files save to: {DOWNLOADS_DIR}", "system")
        
        # Bind resize event
        self.master.bind("<Configure>", self.on_resize)

    # --- CHANGE 2: ADDED THIS HELPER FUNCTION ---
    def _get_avatar_color(self, addr):
        if not addr:
            return self.avatar_colors[0]
        # Use hashlib to create a consistent hash of the address
        hash_val = int(hashlib.md5(addr.encode('utf-8')).hexdigest(), 16)
        color_index = hash_val % len(self.avatar_colors)
        return self.avatar_colors[color_index]
    # --- END OF CHANGE 2 ---

    def on_resize(self, event):
        # This is a simple way to trigger a redraw on resize
        # A more complex app might recalculate layouts here
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # self.gui_queue.put(self.redraw_canvas)
        pass
    
    def _build_ui(self):
        # Main container
        self.main_container = ctk.CTkFrame(self.master, fg_color="transparent")
        self.main_container.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Top Navigation Bar
        self.nav_bar = ctk.CTkFrame(self.main_container, height=60, corner_radius=0,
                                    fg_color=("gray90", "gray17"))
        self.nav_bar.pack(fill="x", side="top", padx=0, pady=0)
        self.nav_bar.pack_propagate(False)
        
        # Logo and Title
        title_frame = ctk.CTkFrame(self.nav_bar, fg_color="transparent")
        title_frame.pack(side="left", padx=20, pady=10)
        
        # --- FIX: MODIFIED THIS BLOCK TO HANDLE LOGO.JPG LOADING ---
        logo_loaded_successfully = False
        try:
            # 1. Load the logo (make sure 'logo.jpg' is in the same folder)
            logo_pil_image = Image.open("icon.jpg") 
            
            # 2. Create the CTkImage object
            logo_image = ctk.CTkImage(
                light_image=logo_pil_image,
                dark_image=logo_pil_image,
                size=(26, 26) # You can adjust the size (width, height) here
            )
            
            # 3. Create a label JUST FOR THE IMAGE
            logo_label = ctk.CTkLabel(title_frame, text="", image=logo_image)
            logo_label.pack(side="left", padx=(0, 8)) # Add 8px padding between logo and text
            logo_loaded_successfully = True

        except FileNotFoundError:
            print("Error: 'logo.jpg' not found in the script directory.")
            print("Please ensure 'logo.jpg' is in the same folder as sh_client.py.")
            # Fallback to a text letter if logo.jpg is missing
            logo_label = ctk.CTkLabel(title_frame, text="H", 
                                      font=ctk.CTkFont(size=22, weight="bold"),
                                      text_color="#3b82f6") # Use a brand color
            logo_label.pack(side="left", padx=(0, 8))
        except Exception as e:
            print(f"Error loading logo.jpg: {e}")
            # Fallback to a text letter if loading fails for other reasons
            logo_label = ctk.CTkLabel(title_frame, text="H", 
                                      font=ctk.CTkFont(size=22, weight="bold"),
                                      text_color="#3b82f6") 
            logo_label.pack(side="left", padx=(0, 8))
        # --- END OF MODIFIED CODE ---
        
        # 4. The original title_label to ONLY have the text
        title_label = ctk.CTkLabel(title_frame, text="HELIX", 
                                   font=ctk.CTkFont(size=22, weight="bold"))
        title_label.pack(side="left")

        # Status indicators
        status_frame = ctk.CTkFrame(self.nav_bar, fg_color="transparent")
        status_frame.pack(side="left", padx=20, pady=10)
        
        self.status_indicator = ctk.CTkLabel(status_frame, text="●", 
                                            font=ctk.CTkFont(size=20),
                                            text_color="#ef4444")
        self.status_indicator.pack(side="left", padx=5)
        
        self.status_label = ctk.CTkLabel(status_frame, text="Disconnected",
                                         font=ctk.CTkFont(size=13))
        self.status_label.pack(side="left")
        
        self.users_label = ctk.CTkLabel(status_frame, text="👥 0 users",
                                       font=ctk.CTkFont(size=13))
        self.users_label.pack(side="left", padx=15)
        
        # Right-aligned nav content
        nav_right_frame = ctk.CTkFrame(self.nav_bar, fg_color="transparent")
        nav_right_frame.pack(side="right", padx=20, pady=10)

        # Connection Frame
        self.conn_frame = ctk.CTkFrame(nav_right_frame, fg_color="transparent")
        self.conn_frame.pack(side="left", padx=10)
        
        self.ip_entry = ctk.CTkEntry(self.conn_frame, placeholder_text="Server IP", width=140)
        self.ip_entry.pack(side="left", padx=5)
        self.ip_entry.insert(0, "127.0.0.1") 
        
        self.name_entry = ctk.CTkEntry(self.conn_frame, placeholder_text="Username", width=120)
        self.name_entry.pack(side="left", padx=5)
        self.name_entry.insert(0, os.getlogin() if hasattr(os, "getlogin") else "user")
        
        self.connect_btn = ctk.CTkButton(self.conn_frame, text="Connect", width=100,
                                         command=self.connect, corner_radius=20,
                                         fg_color="#13ad28", hover_color="#059669")
        self.connect_btn.pack(side="left", padx=5)
        
        # FIX v6: Add Leave button to top bar (initially hidden)
        # CHANGE 1: Changed width from 100 to 80
        self.leave_btn = ctk.CTkButton(nav_right_frame, text="Leave", width=80, height=40,
                                       corner_radius=20, font=ctk.CTkFont(size=14, weight="bold"),
                                       command=self.leave_meeting,
                                       fg_color="#ef4444", hover_color="#dc2626",
                                       state="disabled")
        self.leave_btn.pack(side="left", padx=10, pady=10)
        self.leave_btn.pack_forget() # Hide until connected
        
        # Theme Toggle
        self.theme_btn = ctk.CTkButton(nav_right_frame, text="🌙", width=40, height=40,
                                       corner_radius=20, command=self.toggle_theme,
                                       font=ctk.CTkFont(size=20))
        self.theme_btn.pack(side="left", padx=10)
        
        # FIX 3: Add VBG Toggle button to nav bar
        # CHANGE 3: Replaced CTkButton with a CTkSwitch in a frame
        self.vbg_switch_frame = ctk.CTkFrame(nav_right_frame, fg_color="transparent")
        self.vbg_switch_frame.pack(side="left", padx=10, pady=10)
        
        self.vbg_label = ctk.CTkLabel(self.vbg_switch_frame, text="BG",
                                      font=ctk.CTkFont(size=14))
        self.vbg_label.pack(side="left", padx=5)
        
        self.vbg_toggle_switch = ctk.CTkSwitch(self.vbg_switch_frame, text="", width=0,
                                               command=self.toggle_vbg_state,
                                               state="disabled")
        self.vbg_toggle_switch.pack(side="left")
        
        # --- Bottom Control Bar (DOCKING) ---
        # UPDATE: Changed height to 80, set corner radius, and will .pack() at the end
        self.control_bar = ctk.CTkFrame(self.main_container, height=80,
                                        fg_color=("gray85", "gray20"), corner_radius=20)
        
        # FIX v6: 5 columns, all equal weight, for 5 icons
        self.control_bar.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        self.control_bar.grid_rowconfigure(0, weight=1)

        # --- UPDATE: Rebuilt buttons with labels ---

        # Video Button Container
        video_btn_frame = ctk.CTkFrame(self.control_bar, fg_color="transparent")
        video_btn_frame.grid(row=0, column=0, padx=6)
        #video_btn_frame.pack_propagate(False)
        
        self.video_btn = ctk.CTkButton(video_btn_frame, text="▶", width=50, height=50,
                                       corner_radius=25, font=ctk.CTkFont(size=24),
                                       command=self.toggle_video,
                                       fg_color="#2563EB", hover_color="#1D4ED8",
                                       state="disabled") # FIX 3: Disable on start
        self.video_btn.pack(pady=(5, 2))
        ctk.CTkLabel(video_btn_frame, text="Video", font=ctk.CTkFont(size=11)).pack(pady=(0, 5))
        
        # Audio Button Container
        audio_btn_frame = ctk.CTkFrame(self.control_bar, fg_color="transparent")
        audio_btn_frame.grid(row=0, column=1, padx=6,sticky='')

        self.audio_btn = ctk.CTkButton(audio_btn_frame, text="🔊", width=50, height=50,
                                       corner_radius=25, font=ctk.CTkFont(size=24),
                                       command=self.toggle_audio,
                                       fg_color="#475569", hover_color="#334155",
                                       state="disabled") # FIX 3: Disable on start
        self.audio_btn.pack(pady=(5, 2))
        ctk.CTkLabel(audio_btn_frame, text="Audio", font=ctk.CTkFont(size=11)).pack(pady=(0, 5))

        # Screen Share Button Container
        screen_btn_frame = ctk.CTkFrame(self.control_bar, fg_color="transparent")
        screen_btn_frame.grid(row=0, column=2, padx=6,sticky='')
        
        self.screen_btn = ctk.CTkButton(screen_btn_frame, text="💻", width=50, height=50,
                                        corner_radius=25, font=ctk.CTkFont(size=24),
                                        command=self.toggle_screen_share,
                                        fg_color="#F97316", hover_color="#EA580C",
                                        state="disabled") # FIX v5: Changed emoji
        self.screen_btn.pack(pady=(5, 2))
        ctk.CTkLabel(screen_btn_frame, text="Share", font=ctk.CTkFont(size=11)).pack(pady=(0, 5))
        
        # REMOVED: View Screen Button
        
        # Chat Button (Toggles Sidebar) Container
        # UPDATE: Moved to column 3
        chat_toggle_btn_frame = ctk.CTkFrame(self.control_bar, fg_color="transparent")
        chat_toggle_btn_frame.grid(row=0, column=3, padx=6,sticky='')
        
        self.chat_toggle_btn = ctk.CTkButton(chat_toggle_btn_frame, text="💭", width=50, height=50,
                                            corner_radius=25, font=ctk.CTkFont(size=24),
                                            command=lambda: self.toggle_sidebar("💬 Chat"),
                                            fg_color="#0D9488", hover_color="#0F766E",
                                            state="disabled") # FIX 3: Disable on start
        self.chat_toggle_btn.pack(pady=(5, 2))
        ctk.CTkLabel(chat_toggle_btn_frame, text="Chat", font=ctk.CTkFont(size=11)).pack(pady=(0, 5))

        # FIX v6: Replaced "Users" button with "Effects" (VBG) button
        # Virtual Background Button Container
        vbg_btn_frame = ctk.CTkFrame(self.control_bar, fg_color="transparent")
        vbg_btn_frame.grid(row=0, column=4, padx=6,sticky='')

        self.vbg_btn = ctk.CTkButton(vbg_btn_frame, text="🪄", width=50, height=50,
                                     corner_radius=25, font=ctk.CTkFont(size=24),
                                     command=self.load_vbg_image, # FIX 3: Command changed
                                     fg_color="#7C3AED", hover_color="#6D28D9",
                                     state="disabled")
        self.vbg_btn.pack(pady=(5, 2))
        ctk.CTkLabel(vbg_btn_frame, text="Effects", font=ctk.CTkFont(size=11)).pack(pady=(0, 5))

        # --- End of button updates ---

        # FIX v6: Removed Leave Button from here
        
        # --- Content Area ---
        # This frame holds the canvas and the (hidden) sidebar
        # UPDATE: This now packs *above* the control bar
        self.content_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        
        # Video Canvas (fills the space)
        canvas_bg_color = "#0a0a0a" if self.dark_mode else "#f5f5f5"
        self.canvas = tk.Canvas(self.content_frame, bg=canvas_bg_color, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        
        # --- Right Sidebar (Togglable) ---
        self.sidebar_frame = ctk.CTkFrame(self.content_frame, width=350, corner_radius=0,
                                          fg_color=("gray90", "gray17"))
        # Don't pack() sidebar yet, it's hidden by default

        # Tabview for organized content
        self.tabview = ctk.CTkTabview(self.sidebar_frame, corner_radius=0,
                                      fg_color=("gray90", "gray17"),
                                      segmented_button_fg_color=("gray90", "gray17"),
                                      segmented_button_selected_color="#3b82f6",
                                      segmented_button_unselected_color=("gray80", "gray20"))
        self.tabview.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Chat Tab
        chat_tab = self.tabview.add("💬 Chat")
        chat_tab.configure(fg_color=("gray85", "gray14"))
        
        # Chat target selector
        target_frame = ctk.CTkFrame(chat_tab, fg_color="transparent")
        target_frame.pack(fill="x", pady=(10, 10), padx=10)
        
        ctk.CTkLabel(target_frame, text="To:", font=ctk.CTkFont(size=12)).pack(side="left", padx=5)
        self.chat_target = ctk.CTkComboBox(target_frame, values=["Everyone"], width=150)
        self.chat_target.pack(side="left", fill="x", expand=True, padx=5)
        self.chat_target.set("Everyone")
        
        # Chat display
        chat_display_frame = ctk.CTkFrame(chat_tab, corner_radius=0, fg_color=("gray80", "gray20"))
        chat_display_frame.pack(fill="both", expand=True, pady=0, padx=10)
        
        self.chat_text = ctk.CTkTextbox(chat_display_frame, wrap="word", 
                                        font=ctk.CTkFont(size=12),
                                        fg_color="transparent")
        self.chat_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Emoji Reactions
        emoji_frame = ctk.CTkFrame(chat_tab, fg_color="transparent")
        emoji_frame.pack(fill="x", pady=10, padx=10)
        
        # User requested emojis: 😂🔥❤😭🙁🙏😁👌🥳👍🏻
        # Note: Some unicode emojis may not render the same everywhere.
        # Using common ones:
        reactions = ["😂", "🔥", "❤️", "😭", "🙏", "👍", "😁", "👌", "🥳", "💯"]
        for i, emoji in enumerate(reactions):
            row = i // 5
            col = i % 5
            btn = ctk.CTkButton(emoji_frame, text=emoji, width=35, height=35,
                              corner_radius=17, font=ctk.CTkFont(size=18),
                              command=lambda e=emoji: self.send_emoji_reaction(e),
                              fg_color="transparent", hover_color="#374151")
            btn.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        emoji_frame.grid_columnconfigure((0,1,2,3,4), weight=1)

        # Chat input
        input_frame = ctk.CTkFrame(chat_tab, fg_color="transparent")
        input_frame.pack(fill="x", pady=10, padx=10)
        
        self.msg_entry = ctk.CTkEntry(input_frame, placeholder_text="Type a message...",
                                      height=40, corner_radius=20)
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.msg_entry.bind('<Return>', lambda e: self.send_chat())
        
        ctk.CTkButton(input_frame, text="Send", width=70, height=40,
                     corner_radius=20, command=self.send_chat,
                     fg_color="#10b981", hover_color="#059669").pack(side="right")
        
        # Users Tab
        users_tab = self.tabview.add("👥 Users")
        users_tab.configure(fg_color=("gray85", "gray14"))
        
        users_display_frame = ctk.CTkFrame(users_tab, corner_radius=0, fg_color=("gray80", "gray20"))
        users_display_frame.pack(fill="both", expand=True, pady=10, padx=10)
        
        self.users_textbox = ctk.CTkTextbox(users_display_frame, wrap="word",
                                           font=ctk.CTkFont(size=13),
                                           fg_color="transparent")
        self.users_textbox.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Files Tab
        files_tab = self.tabview.add("📁 Files")
        files_tab.configure(fg_color=("gray85", "gray14"))
        
        files_content_frame = ctk.CTkFrame(files_tab, fg_color="transparent")
        files_content_frame.pack(fill="both", expand=True, pady=10, padx=10)

        ctk.CTkButton(files_content_frame, text="📤 Send File", height=50,
                     corner_radius=10, command=self.send_file,
                     fg_color="#3b82f6", hover_color="#2563eb",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(fill="x", pady=10)
        
        ctk.CTkButton(files_content_frame, text="📂 Open Downloads", height=50,
                     corner_radius=10, command=self.open_downloads,
                     fg_color="#8b5cf6", hover_color="#7c3aed",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(fill="x", pady=10)
        
        # FIX 3: Removed "Select Background" button from Files tab
        # ctk.CTkButton(files_content_frame, text="🖼️ Select Background", ...).pack(...)
        
        # Activity Log
        log_frame = ctk.CTkFrame(files_content_frame, corner_radius=10, fg_color=("gray80", "gray20"))
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        
        ctk.CTkLabel(log_frame, text="📊 Activity Log", 
                    font=ctk.CTkFont(size=14, weight="bold")
                    ).pack(pady=5)
        
        self.log_textbox = ctk.CTkTextbox(log_frame, wrap="word",
                                         font=ctk.CTkFont(size=11),
                                         fg_color="transparent")
        self.log_textbox.pack(fill="both", expand=True, padx=5, pady=5)


        # --- FINAL PACKING ORDER ---
        # 1. Top Nav Bar (already packed)
        # 2. Bottom Control Bar (pack this next, so it's at the bottom)
        self.control_bar.pack(side="bottom", fill="x", pady=10, padx=10)
        # 3. Content Frame (pack last, to fill remaining space)
        self.content_frame.pack(side="top", fill="both", expand=True, padx=0, pady=0)


        # Removed all Feedback Page build logic

        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
    
    # Removed: All Feedback methods (_build_feedback_page, show/hide, update, submit)

    def toggle_sidebar(self, tab_to_select=None):
        if self.sidebar_visible:
            # If the sidebar is visible and we clicked the *same* button,
            # or we didn't specify a tab, hide it.
            if tab_to_select is None or tab_to_select == self.tabview.get():
                self.sidebar_frame.pack_forget()
                self.sidebar_visible = False
            else:
                # Sidebar is visible, but we want a *different* tab
                self.tabview.set(tab_to_select)
        else:
            # Sidebar is hidden, so show it.
            self.sidebar_frame.pack(side="right", fill="y", padx=0, pady=0)
            self.sidebar_visible = True
            if tab_to_select:
                self.tabview.set(tab_to_select)
        
        # Trigger a canvas redraw to account for new layout
        # This is crucial so the canvas knows its new size
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # self.master.after(50, lambda: self.gui_queue.put(self.redraw_canvas))


    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        if self.dark_mode:
            ctk.set_appearance_mode("dark")
            self.theme_btn.configure(text="🌙")
            self.canvas.configure(bg="#0a0a0a")
        else:
            ctk.set_appearance_mode("light")
            self.theme_btn.configure(text="☀️")
            self.canvas.configure(bg="#f5f5f5")
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # self.redraw_canvas()
    
    def reaction_updater_loop(self):
        if not self.running:
            return
            
        now = time.time()
        needs_redraw = False
        
        # Use list comprehension to filter out expired reactions
        active_count_before = len(self.active_reactions)
        self.active_reactions = [
            r for r in self.active_reactions
            if (now - r["start_time"]) < REACTION_DURATION_SECONDS
        ]
        
        # If any reactions are active OR if any were just removed
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # if len(self.active_reactions) > 0 or active_count_before > len(self.active_reactions):
        #     needs_redraw = True
            
        # if needs_redraw:
        #     self.gui_queue.put(self.redraw_canvas)
        
        # Schedule the next check
        self.master.after(50, self.reaction_updater_loop) # 20fps for animation
    
    # FIX v8.0: New central canvas update loop
    def canvas_updater_loop(self):
        if not self.running:
            return
        
        # Try to redraw
        try:
            self.redraw_canvas()
        except Exception as e:
            if self.running: # Only log if we're not shutting down
                print(f"Canvas redraw error: {e}")
        
        # Schedule the next frame
        self.master.after(33, self.canvas_updater_loop) # ~30 FPS

    def send_emoji_reaction(self, emoji):
        if not self.connected:
            return
        
        # FIX 3: Render emoji locally *immediately*
        try:
            # CHANGE 2: Removed x_pos calculation. Just store addr.
            self.active_reactions.append({
                "emoji": emoji,
                "addr": self.my_addr, # Tag as MINE
                "start_time": time.time(),
            })
        except:
            pass # Canvas might not be ready
        
        # This function now triggers the local effect AND sends the message
        self.send_reaction_message(emoji)
    
    def send_reaction_message(self, emoji):
        if not self.tcp_sock or not self.connected or not self.my_addr:
            return
        
        try:
            # 1. Trigger local effect immediately
            # We don't need to add our own reaction locally,
            # the server will broadcast it back to us.
            # FIX 3: Local effect is now handled in send_emoji_reaction
            
            # 2. Send to server for broadcast
            self.tcp_sock.sendall(pack_control({
                "type": "reaction", 
                "emoji": emoji,
                "addr": self.my_addr # Tell others who sent it
            }))
        except Exception as e:
            self.log(f"✗ Send reaction failed: {e}", "error")
            self.gui_queue.put(self.cleanup_connection)

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
            self.log(f"✗ Send failed: {e}", "error")
            self.gui_queue.put(self.cleanup_connection)
    
    def process_gui_queue(self):
        try:
            while True:
                callback = self.gui_queue.get_nowait()
                if callable(callback):
                    callback()
        except Queue.Empty:
            pass
        finally:
            if self.running:
                self.master.after(20, self.process_gui_queue)
    
    def update_users_display(self):
        self.users_textbox.delete("1.0", "end")
        
        # Show current user first
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
        self.users_label.configure(text=f"👥 {total_users} user{'s' if total_users != 1 else ''}")
    
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
            self.status_indicator.configure(text_color="#10b981")
            self.leave_btn.configure(state="normal")
            self.connect_btn.configure(state="disabled")
            self.conn_frame.pack_forget() # Hide connection inputs
            self.leave_btn.pack(side="left", padx=10, pady=10) # FIX v6: Show Leave button
            
            if not self.audio_play_stream and PYAUDIO_AVAILABLE:
                self.start_audio_playback()
            
            self.log(f"✓ Connected as {username}", "success")
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
    
    def leave_meeting(self):
        if not self.connected:
            return
        
        if messagebox.askyesno("Leave Meeting", "Are you sure you want to leave?"):
            try:
                if self.tcp_sock:
                    self.tcp_sock.sendall(pack_control({"type": "bye"}))
            except:
                pass
            self.cleanup_connection()
            self.log("✓ Left meeting", "system")
    
    def cleanup_connection(self):
        self.connected = False
        self.sending_video = False
        self.sending_audio = False
        
        # Stop all streams and cleanup sockets
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
        self.status_indicator.configure(text_color="#ef4444")
        self.leave_btn.configure(state="disabled")
        self.connect_btn.configure(state="normal")
        self.conn_frame.pack(side="left", padx=10) # Show connection inputs
        self.leave_btn.pack_forget() # FIX v6: Hide Leave button
        
        # Reset button states
        self.video_btn.configure(fg_color="#3b82f6", state="disabled") # FIX 3: Disable on cleanup
        self.audio_btn.configure(fg_color="#8b5cf6", state="disabled") # FIX 3: Disable on cleanup
        self.screen_btn.configure(fg_color="#f59e0b", state="disabled", text="📤") # FIX v5: Disable/reset on cleanup
        self.chat_toggle_btn.configure(state="disabled") # FIX 3: Disable on cleanup
        
        # FIX v7: Reset VBG button and state
        self.vbg_btn.configure(state="disabled", fg_color="#10b981")
        self.vbg_state = None
        self.vbg_image_raw = None
        self.vbg_image_cv = None
        
        # FIX 3: Reset new VBG toggle button
        # CHANGE 3: Update switch
        self.vbg_toggle_switch.configure(state="disabled")
        self.vbg_toggle_switch.deselect()
        
        # Hide sidebar
        if self.sidebar_visible:
            self.toggle_sidebar()
        
        # Clear state
        self.frames_by_src.clear()
        self.video_frames.clear()
        self.screen_frame = None
        self.current_presenter_addr = None
        self.active_users = []
        self.active_users_dict = {}
        self.video_states = {}
        self.active_reactions.clear()
        self.my_addr = None
        
        # FIX 4: Clear active speaker state
        self.active_speaker_addr = None
        if self.active_speaker_timer:
            self.master.after_cancel(self.active_speaker_timer)
            self.active_speaker_timer = None
        
        self.chat_target.configure(values=["Everyone"])
        self.chat_target.set("Everyone")
        self.update_users_display()
        
        # Close TCP socket
        try:
            if self.tcp_sock:
                self.tcp_sock.close()
                self.tcp_sock = None
        except:
            pass
        
        # Wait for sockets to fully close
        time.sleep(0.5)
        
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # self.gui_queue.put(self.redraw_canvas)
    
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
                        self.log("✗ Disconnected from server", "error")
                        self.gui_queue.put(self.cleanup_connection)
                        break
                    
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        
                        try:
                            msg = json.loads(line.decode())
                            self.gui_queue.put(lambda m=msg: self.handle_control_message(m))
                        except:
                            continue
                
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.connected:
                        self.log(f"✗ TCP error: {e}", "error")
                        self.gui_queue.put(self.cleanup_connection)
                    break
    
    # FIX 4: Callback to clear active speaker
    def clear_active_speaker(self):
        self.active_speaker_addr = None
        self.active_speaker_timer = None
        # Redraw is handled by the main loop

    def handle_control_message(self, msg):
        mtype = msg.get("type")
        
        if mtype == "chat":
            frm = msg.get("from")
            text = msg.get("message")
            self._update_chat(f"{frm}: {text}")

        # New: Handle dedicated reaction
        elif mtype == "reaction":
            frm = msg.get("from")
            addr = msg.get("addr")
            emoji = msg.get("emoji")
            
            # FIX 2: Do not add our own reaction again, we already added it locally
            if addr == self.my_addr:
                return # Changed from 'continue' to 'return' as this is a func

            if addr and emoji:
                # Add to our list for floating animation
                try:
                    # CHANGE 2: Removed x_pos calculation. Just store addr.
                    self.active_reactions.append({
                        "emoji": emoji,
                        "addr": addr,
                        "start_time": time.time(),
                    })
                except:
                    pass # Canvas might not be ready
                
                # Still log it to chat
                # FIX 3: Server now excludes sender, so no local echo needed
                # self._update_chat(f"{frm}: {emoji}")
        
        elif mtype == "private_chat":
            frm = msg.get("from")
            text = msg.get("message")
            self._update_chat(f"🔒 {frm} (private): {text}")
        
        elif mtype == "private_chat_sent":
            to = msg.get("to")
            text = msg.get("message")
            self._update_chat(f"🔒 You to {to}: {text}")
        
        elif mtype == "user_list":
            users = msg.get("users", [])
            if self.username:
                # Find self and store address
                my_info = next((u for u in users if u.get("name") == self.username), None)
                if my_info:
                    # FIX 3: Check if this is the first time setting my_addr
                    is_first_init = self.my_addr is None
                    self.my_addr = my_info.get("addr")
                    
                    if is_first_init and self.my_addr:
                        self.log("✓ Ready to stream.", "success")
                        self.video_btn.configure(state="normal")
                        self.audio_btn.configure(state="normal")
                        self.screen_btn.configure(state="normal")
                        self.chat_toggle_btn.configure(state="normal")
                        # FIX v6: Enable VBG button only if mediapipe is available
                        self.vbg_btn.configure(state="normal" if MEDIAPIPE_AVAILABLE else "disabled")
                        # FIX 3: Enable new VBG toggle button
                        # CHANGE 3: Update switch
                        self.vbg_toggle_switch.configure(state="normal" if MEDIAPIPE_AVAILABLE else "disabled")
                
                self.active_users_dict = [u for u in users if u.get("name") != self.username]
            else:
                self.active_users_dict = users
            
            # Update video states for new users
            for u in users:
                self.video_states.setdefault(u.get('addr'), True)
            
            self.active_users = [u.get("name") for u in self.active_users_dict]
            self.chat_target.configure(values=['Everyone'] + self.active_users)
            
            if self.chat_target.get() not in (['Everyone'] + self.active_users):
                self.chat_target.set("Everyone")
            
            self.update_users_display()
            # FIX v8.0: Redraw is handled by the canvas_updater_loop
            # self.gui_queue.put(self.redraw_canvas) # Redraw to add new user placeholders
        
        elif mtype == "join":
            self.log(f"→ {msg.get('name')} joined", "system")
            self.video_states[msg.get('addr')] = True # New user video is on by default
        
        elif mtype == "leave":
            name = msg.get('name')
            addr = msg.get('addr')
            self.log(f"← {name} left", "system")
            
            # Clean up user state
            self.video_states.pop(addr, None)
            self.frames_by_src.pop(addr, None)
            # Remove any active reactions from this user
            self.active_reactions = [r for r in self.active_reactions if r.get("addr") != addr]
            # FIX v8.0: Redraw is handled by the canvas_updater_loop
            # self.gui_queue.put(self.redraw_canvas) # Redraw to remove user
        
        # New: Handle video state changes
        elif mtype == "video_start":
            self.video_states[msg.get("addr")] = True
            self.log(f"📹 {msg.get('from')} started video", "system")

        elif mtype == "video_stop":
            self.video_states[msg.get("addr")] = False
            self.frames_by_src.pop(msg.get("addr"), None) # Remove last frame
            self.log(f"📹 {msg.get('from')} stopped video", "system")
            # FIX v8.0: Redraw is handled by the canvas_updater_loop
            # self.gui_queue.put(self.redraw_canvas) # Redraw to show placeholder
        
        elif mtype == "file_offer":
            frm = msg.get("from")
            fname = msg.get("filename")
            size = msg.get("size")
            self.show_file_offer(frm, fname, size)
        
        elif mtype == "error":
            self.log(f"⚠ {msg.get('message', '')}", "error")
            if "Username already taken" in msg.get('message', ''):
                self.cleanup_connection()
                messagebox.showerror("Error", "Username is already taken.")
        
        elif mtype == "present_start":
            self.log(f"🖥 {msg.get('from')} started presenting", "system")
            # UPDATE: Auto-start viewing
            # FIX 1: Don't start viewing if we are the one sharing
            if not self.viewing_screen and not self.sharing_screen:
                self.log("👁 Auto-starting screen view...", "system")
                threading.Thread(target=self.ss_view_wrapper, daemon=True).start()
        
        elif mtype == "present_stop":
            # This message is now received by all, including the presenter
            # The presenter's own stop logic is in _stop_screen_share
            # This handler is for *other* clients
            self.log(f"🖥 {msg.get('from')} stopped presenting", "system")
            if self.viewing_screen and not self.sharing_screen:
                # We are a viewer, and not the presenter.
                # The screen_receive_loop will handle the disconnection.
                # We just log it.
                pass
        
        # FIX 4: Handle Active Speaker message
        elif mtype == "speaker_active":
            addr = msg.get("addr")
            if addr:
                self.active_speaker_addr = addr
                
                # FIX 4: Set a timer to clear the active speaker status
                if self.active_speaker_timer:
                    try:
                        self.master.after_cancel(self.active_speaker_timer)
                    except: pass
                
                # Clear after 1.5s (server sends ~every 1s)
                self.active_speaker_timer = self.master.after(1500, self.clear_active_speaker)

    def _update_chat(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.chat_text.insert("end", f"[{timestamp}] {text}\n")
        self.chat_text.see("end")
    
    def show_file_offer(self, from_user, filename, size):
        size_mb = size / (1024 * 1024)
        msg = f"{from_user} wants to share:\n\n{filename}\nSize: {size_mb:.2f} MB\n\nDownload?"
        
        if messagebox.askyesno("File Offer", msg):
            self.log(f"📥 Downloading {filename}...", "system")
            threading.Thread(target=self.download_file, args=(filename,), daemon=True).start()
        else:
            self.log(f"✗ Declined {filename}", "system")
    
    def download_file(self, filename):
        try:
            file_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            file_sock.settimeout(30.0)
            file_sock.connect((self.server_ip, FILE_TCP_PORT))
            
            request = json.dumps({"type": "file_download", "filename": filename}).encode()
            file_sock.sendall(request)
            
            info_data = file_sock.recv(4096)
            if info_data == b"ERROR":
                self.log(f"✗ File not found: {filename}", "error")
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
            self.log(f"✓ Downloaded: {filename}", "success")
        
        except Exception as e:
            self.log(f"✗ Download error: {e}", "error")
    
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
            self.log(f"✗ Send failed: {e}", "error")
            self.gui_queue.put(self.cleanup_connection)
    
    def send_file(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        path = filedialog.askopenfilename(title="Select File")
        if not path:
            return
        
        threading.Thread(target=self.ss_upload_file_wrapper, args=(path,), daemon=True).start()
    
    def ss_upload_file_wrapper(self, path):
        self._upload_file(path)

    def _upload_file(self, path):
        try:
            name = os.path.basename(path)
            size = os.path.getsize(path)
            self.log(f"📤 Uploading {name}...", "system")
            
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
            self.log(f"✓ Upload complete: {name}", "success")
        
        except Exception as e:
            self.log(f"✗ Upload failed: {e}", "error")
    
    def toggle_video(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        if not self.sending_video:
            self.sending_video = True
            self.video_btn.configure(fg_color="#dc2626", hover_color="#b91c1c")
            threading.Thread(target=self.video_send_loop, daemon=True).start()
            self.log("📹 Video started", "success")
            try:
                self.tcp_sock.sendall(pack_control({"type": "video_start"}))
            except:
                pass
        else:
            self.sending_video = False
            self.video_btn.configure(fg_color="#3b82f6", hover_color="#2563eb")
            self.log("📹 Video stopped", "system")
            try:
                self.tcp_sock.sendall(pack_control({"type": "video_stop"}))
            except:
                pass
            
            # FIX: Logic moved to video_send_loop finally block
            # self.frames_by_src.pop(self.my_addr, None)
            # self.gui_queue.put(self.redraw_canvas)
    
    def video_send_loop(self):
        cap = None
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                self.log("✗ Cannot open camera", "error")
                self.gui_queue.put(self.toggle_video) # Toggle back off
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
                
                # --- VIRTUAL BACKGROUND (FIX v7 - Custom Image) ---
                # FIX v7.3: Added 'and not self.sharing_screen' to prevent resource conflict
                if (self.vbg_state == "image" and self.vbg_image_cv is not None 
                    and self.segmentation and not self.sharing_screen):
                    try:
                        # Convert to RGB, process, and get mask
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        frame_rgb.flags.writeable = False # Performance optimization
                        results = self.segmentation.process(frame_rgb)
                        frame_rgb.flags.writeable = True
                        mask = results.segmentation_mask

                        # Use pre-resized custom image as background
                        background = self.vbg_image_cv

                        # Composite
                        condition = np.stack((mask,) * 3, axis=-1) > 0.6 # Threshold
                        frame = np.where(condition, frame, background)
                    except Exception as e:
                        self.log(f"✗ VBG Error: {e}", "error")
                        self.vbg_state = None # Disable on error
                        # FIX 3: Update correct button
                        # CHANGE 3: Update switch
                        self.gui_queue.put(lambda: self.vbg_toggle_switch.deselect())
                # --- END VIRTUAL BACKGROUND ---
                
                # --- FIX: ADDED SELF-VIEW LOGIC ---
                try:
                    # Create frame for self-view
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) # frame is already BGR
                    pil = Image.fromarray(frame_rgb)
                    self.frames_by_src[self.my_addr] = pil
                    
                    # Trigger a redraw
                    # FIX v8.0: Redraw is handled by the canvas_updater_loop
                    # current_time = time.time()
                    # if current_time - self.last_canvas_update >= self.canvas_update_interval:
                    #     self.last_canvas_update = current_time
                    #     self.gui_queue.put(self.redraw_canvas)
                        
                except Exception as e:
                    print(f"Self-view error: {e}")
                # --- END OF FIX ---

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
                        video_send_sock.sendto(header + chunk, (self.server_ip, VIDEO_UDP_PORT))
                    except:
                        pass
                
                frame_id = (frame_id + 1) & 0xFFFFFFFF
                
                elapsed = time.time() - start
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        except Exception as e:
            self.log(f"✗ Video send error: {e}", "error")
        
        finally:
            if cap:
                cap.release()
            self.sending_video = False
            
            # FIX: Clean up self-view frame and redraw
            # This is the guaranteed "last" thing to run.
            self.frames_by_src.pop(self.my_addr, None)
            # FIX v8.0: Redraw is handled by the canvas_updater_loop
            # self.gui_queue.put(self.redraw_canvas)
    
    def video_receiver_loop(self):
        while self.running:
            try:
                pkt, addr = video_recv_sock.recvfrom(MAX_UDP_SIZE)
                
                if not pkt or len(pkt) < 12:
                    continue
                
                src_ip_packed = pkt[:4]
                src_ip = socket.inet_ntoa(src_ip_packed)
                
                # Check if video is enabled for this source
                if not self.video_states.get(src_ip, True):
                    continue # Drop packet if video is off
                
                frame_id, total_parts, part_idx = struct.unpack("!IHH", pkt[4:12])
                payload = pkt[12:]
                
                key = (src_ip, frame_id)
                
                if key not in self.video_frames:
                    if len(self.video_frames) > 20:
                        self.video_frames.clear()
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
                    self.frames_by_src[src_ip] = pil
                    
                    # FIX v8.0: Redraw is handled by the canvas_updater_loop
                    # current_time = time.time()
                    # if current_time - self.last_canvas_update >= self.canvas_update_interval:
                    #     self.last_canvas_update = current_time
                    #     self.gui_queue.put(self.redraw_canvas)
            
            except Exception as e:
                time.sleep(0.001)
    
    # --- CHANGE 3: MODIFIED THIS FUNCTION ---
    def _draw_video_tile(self, user, x, y, cw, ch):
        label_addr = user.get("addr")
        label_text = user.get("name")
        is_self = user.get("is_self", False)
        
        # Determine video state
        video_on = self.video_states.get(label_addr, True) if not is_self else self.sending_video
        pil = self.frames_by_src.get(label_addr)
        
        # FIX 4: Active Speaker Highlight
        if label_addr == self.active_speaker_addr:
            border_color = "#10b981" # Bright green
            border_width = 4
        else:
            border_color = "#333333" if self.dark_mode else "#cccccc"
            border_width = 2
        
        bg_color = "#1a1a1a" if self.dark_mode else "#e5e5e5"
        
        self.canvas.create_rectangle(x + 2, y + 2, x + cw - 2, y + ch - 2,
                                    fill=bg_color, outline=border_color, width=border_width)
        
        if pil and (video_on or (is_self and self.sending_video)):
            try:
                # Maintain aspect ratio
                img_w, img_h = pil.size
                scale = min((cw - 8) / img_w, (ch - 8) / img_h)
                new_w, new_h = int(img_w * scale), int(img_h * scale)
                
                img = pil.resize((new_w, new_h), Image.Resampling.BILINEAR)
                tkimg = ImageTk.PhotoImage(img)
                self.canvas.create_image(x + cw//2, y + ch//2, image=tkimg)
                self.canvas._imgs.append(tkimg)
            except:
                pass # Error resizing
        else:
            # --- NEW GOOGLE-MEET-STYLE PLACEHOLDER ---
            try:
                # 1. Get initial
                initial = label_text[0].upper() if label_text else "?"
                
                # 2. Get color
                # This relies on the _get_avatar_color function and self.avatar_colors list!
                color = self._get_avatar_color(label_addr)
                
                # 3. Calculate circle size
                circle_diameter = min(cw, ch) * 0.5 # 50% of the smallest dimension
                radius = circle_diameter / 2
                
                # 4. Draw circle
                self.canvas.create_oval(
                    x + cw//2 - radius, y + ch//2 - radius,
                    x + cw//2 + radius, y + ch//2 + radius,
                    fill=color,
                    outline=""
                )
                
                # 5. Draw initial
                font_size = int(circle_diameter * 0.5) # 50% of the circle size
                
                # Use the font family if it exists, otherwise default to 'Segoe UI'
                font_family = getattr(self, "bold_font_family", "Segoe UI")
                
                self.canvas.create_text(
                    x + cw//2, y + ch//2,
                    text=initial,
                    fill="white",
                    font=(font_family, font_size, 'bold'),
                    anchor=tk.CENTER
                )
            except Exception as e:
                # Fallback in case of error (e.g., self._get_avatar_color not added yet)
                print(f"Error drawing avatar: {e}") # Added a print for debugging
                placeholder_text_color = "#666666" if self.dark_mode else "#999999"
                font_family_fallback = getattr(self, "bold_font_family", "Segoe UI")
                self.canvas.create_text(x + cw//2, y + ch//2,
                                       text=f"{label_text}\n(Video Off)", 
                                       fill=placeholder_text_color, 
                                       font=(font_family_fallback, 12, 'bold'),
                                       justify=tk.CENTER)
            # --- END NEW PLACEHOLDER LOGIC ---

        # Draw name badge
        # Use the font family if it exists, otherwise default to 'Segoe UI'
        font_family_name_badge = getattr(self, "bold_font_family", "Segoe UI")
        self.canvas.create_text(x + 12, y + ch - 15, text=label_text, fill="white",
                               anchor=tk.SW, font=(font_family_name_badge, 11, 'bold'))
    # --- END OF CHANGE 3 ---

    def redraw_canvas(self):
        try:
            self.canvas.delete("all")
        except:
            return # Canvas may be destroyed
        
        if not hasattr(self.canvas, '_imgs'):
            self.canvas._imgs = []
        self.canvas._imgs.clear()

        # Build list of users to draw
        users_to_draw = []
        if self.my_addr:
            users_to_draw.append({
                "addr": self.my_addr,
                "name": f"{self.username} (You)",
                "is_self": True
            })
        users_to_draw.extend(self.active_users_dict)
        n = len(users_to_draw)

        try:
            canvas_total_width = self.canvas.winfo_width()
            canvas_total_height = self.canvas.winfo_height()
        except:
            return # Canvas not ready

        if canvas_total_width <= 10 or canvas_total_height <= 10:
            return # Canvas not ready

        # --- UPDATE: New rendering logic ---
        
        # CHANGE 2: Add dictionary to store tile coordinates
        user_tile_coords = {}
        
        if self.screen_maximized and self.screen_frame:
            # --- Screen Share View ---
            
            # 1. Calculate layout
            participant_panel_width = int(canvas_total_width * 0.20) # 20% for participants
            screen_width = canvas_total_width - participant_panel_width
            screen_height = canvas_total_height
            
            # 2. Draw Shared Screen
            try:
                img = self.screen_frame.resize((screen_width, screen_height), Image.Resampling.LANCZOS)
                tkimg = ImageTk.PhotoImage(img)
                self.canvas.create_image(screen_width//2, screen_height//2, image=tkimg)
                self.canvas._imgs.append(tkimg)
            except:
                pass
            
            # 3. Draw Participant Sidebar on the canvas
            self.canvas.create_rectangle(screen_width, 0, canvas_total_width, canvas_total_height,
                                         fill=("#1a1a1a" if self.dark_mode else "#e5e5e5"),
                                         outline="")
            
            if n > 0:
                # Calculate tile size
                tile_width = participant_panel_width - 10 # 5px padding each side
                tile_height = int(tile_width * (VIDEO_HEIGHT / VIDEO_WIDTH)) # 4:3 aspect ratio
                
                y_offset = 5
                for user in users_to_draw:
                    x = screen_width + 5
                    y = y_offset
                    
                    # CHANGE 2: Store coordinates
                    user_tile_coords[user.get("addr")] = (x, y, tile_width, tile_height)
                    
                    self._draw_video_tile(user, x, y, tile_width, tile_height)
                    y_offset += tile_height + 5
                    if y_offset > canvas_total_height:
                        break # Stop drawing if we run out of space
            
        else:
            # --- Video Grid View ---
            if n == 0:
                bg_color = "#666666" if self.dark_mode else "#999999"
                self.canvas.create_text(canvas_total_width//2, canvas_total_height//2,
                                        text="Waiting to connect...", fill=bg_color, 
                                        font=('Segoe UI', 16))
                # This return was missing, causing a bug if n=0
                # But we should draw emojis even if n=0
                # return 
            
            # Smart layout
            if n == 0: # Handle n=0 case for layout
                cols, rows = 1, 1
            elif n == 1:
                cols, rows = 1, 1
            elif n == 2:
                cols, rows = 2, 1
            elif n <= 4:
                cols, rows = 2, 2
            elif n <= 6:
                cols, rows = 3, 2
            elif n <= 9:
                cols, rows = 3, 3
            else:
                cols = 4
                rows = int(np.ceil(n / cols))

            cw = canvas_total_width // cols
            ch = canvas_total_height // rows
            
            idx = 0
            for r in range(rows):
                for c in range(cols):
                    if idx >= n:
                        break
                    
                    user = users_to_draw[idx]
                    x = c * cw
                    y = r * ch
                    
                    # CHANGE 2: Store coordinates
                    user_tile_coords[user.get("addr")] = (x, y, cw, ch)
                    
                    self._draw_video_tile(user, x, y, cw, ch)
                    idx += 1

        # --- CHANGE 2: REPLACED EMOJI ANIMATION LOGIC ---
        try:
            current_time = time.time()
            for reaction in self.active_reactions:
                coords = user_tile_coords.get(reaction["addr"])
                if not coords:
                    continue # User tile not visible, don't draw emoji
                
                x, y, cw, ch = coords
                
                age = current_time - reaction["start_time"]
                progress = age / REACTION_DURATION_SECONDS
                
                if 0 <= progress <= 1:
                    # New animation: Bounce in, hold, fade out
                    max_font_size = 80
                    scale = 0.0
                    
                    # Define animation phases (as percentages of total duration)
                    bounce_in_end = 0.2  # 0.0s - 0.3s (based on 1.5s duration)
                    bounce_settle_end = 0.4 # 0.3s - 0.6s
                    hold_end = 0.6          # 0.6s - 0.9s
                    # Fade out is from 0.9s - 1.5s
                    
                    if progress < bounce_in_end:
                        # 1. Bounce In (0 -> 1.2x size)
                        t = progress / bounce_in_end
                        scale = t * 1.2
                    elif progress < bounce_settle_end:
                        # 2. Settle (1.2x -> 1.0x size)
                        t = (progress - bounce_in_end) / (bounce_settle_end - bounce_in_end)
                        scale = 1.2 - t * 0.2
                    elif progress < hold_end:
                        # 3. Hold (1.0x size)
                        scale = 1.0
                    else:
                        # 4. Fade Out (1.0x -> 0.0x size)
                        t = (progress - hold_end) / (1.0 - hold_end)
                        scale = 1.0 - t
                    
                    font_size = int(max_font_size * scale)
                    
                    if font_size > 0:
                        # Position is now static in the middle of the tile
                        x_pos = x + cw // 2
                        y_pos = y + ch // 2
                        color = "#FFD700" # Bright yellow

                        self.canvas.create_text(
                            x_pos, y_pos,
                            text=reaction["emoji"],
                            font=('Segoe UI Emoji', font_size, 'bold'),
                            fill=color,
                            anchor=tk.CENTER
                        )
        except Exception as e:
            # print(f"Error drawing reaction: {e}")
            pass
        # --- End of change ---

    
    def toggle_audio(self):
        if not self.connected:
            messagebox.showerror("Not connected", "Connect first")
            return
        
        if not PYAUDIO_AVAILABLE:
            messagebox.showerror("Audio library missing", "pyaudio not installed")
            return
        
        if not self.sending_audio:
            self.sending_audio = True
            self.audio_btn.configure(fg_color="#dc2626", hover_color="#b91c1c")
            threading.Thread(target=self.audio_capture_loop, daemon=True).start()
            self.log("🎤 Mic ON", "success")
        else:
            self.sending_audio = False
            self.audio_btn.configure(fg_color="#8b5cf6", hover_color="#7c3aed")
            self.stop_audio_capture()
            self.log("🎤 Mic OFF", "system")
    
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
                        audio_send_sock.sendto(data, (self.server_ip, AUDIO_UDP_PORT))
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
            self.gui_queue.put(lambda: self.audio_btn.configure(fg_color="#8b5cf6"))
    
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
            
            self.log("🔊 Audio playback active", "success")
        
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
    
    # FIX 3: This function now *only* loads an image.
    def load_vbg_image(self):
        if not MEDIAPIPE_AVAILABLE:
            messagebox.showwarning("Feature Disabled",
                                 "The Virtual Background feature requires the 'mediappe' library.\n\nRun `pip install mediapipe` and restart the app.")
            self.vbg_btn.configure(state="disabled")
            return

        path = filedialog.askopenfilename(
            title="Select Background Image",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp")]
        )
        if not path:
            # User cancelled
            return
        
        try:
            self.vbg_image_raw = Image.open(path).convert("RGB")
            
            # Pre-resize the image for OpenCV
            bg_pil_resized = self.vbg_image_raw.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
            
            # Convert to OpenCV format (BGR) and store
            self.vbg_image_cv = cv2.cvtColor(np.array(bg_pil_resized), cv2.COLOR_RGB2BGR)
            
            self.log("✓ Custom background image loaded.", "success")
            messagebox.showinfo("Success", "Custom background image loaded.\nUse the 'BG' switch in the top bar to enable it.")
            
            # FIX 3: Do not automatically enable. The user will use the toggle.
            
        except Exception as e:
            self.log(f"✗ Failed to load image: {e}", "error")
            self.vbg_image_cv = None
            self.vbg_image_raw = None
            messagebox.showerror("Image Error", f"Failed to load image: {e}")
    
    
    # FIX 3: New function to *only* toggle the VBG state
    # CHANGE 3: Rewritten to use the CTkSwitch
    def toggle_vbg_state(self):
        if not MEDIAPIPE_AVAILABLE:
            messagebox.showwarning("Feature Disabled",
                                 "The Virtual Background feature requires the 'mediapipe' library.\n\nRun `pip install mediapipe` and restart the app.")
            self.vbg_toggle_switch.configure(state="disabled")
            self.vbg_toggle_switch.deselect()
            return

        # Read the switch state
        is_on = self.vbg_toggle_switch.get() == 1
        
        if is_on:
            # Trying to turn ON
            if self.vbg_image_cv is None:
                messagebox.showinfo("No Image", "Please select a background image using the 'Effects' (🪄) button first.")
                self.vbg_toggle_switch.deselect() # Turn it back off
                return
            
            # Image is loaded, so turn on
            self.vbg_state = "image"
            self.log("✨ Custom background enabled", "system")
        else:
            # Trying to turn OFF
            self.vbg_state = None
            self.log("✨ Background effects disabled", "system")
    
    # FIX 3: Removed old toggle_vbg function
    
    # ===== Robust Screen Share Socket Cleanup =====
    
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
                self.log("🖥 Share socket closed", "system")
        
        # FIX v7.3: Log VBG re-enable
        if self.vbg_state == "image":
            self.log("✨ VBG re-enabled after screen share.", "system")
        
        # FIX v5: Ensure button is always reset to correct state
        self.gui_queue.put(lambda: self.screen_btn.configure(text="📤", fg_color="#f59e0b", hover_color="#d97706", state="normal" if self.connected else "disabled"))

    def _cleanup_view_socket(self):
        with self.screen_share_lock:
            self.viewing_screen = False
            self.screen_maximized = False
            self.screen_frame = None
            self.current_presenter_addr = None # FIX 2: Clear presenter
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
                self.log("👁 View socket closed", "system")
        
        # self.gui_queue.put(lambda: self.view_screen_btn.configure(fg_color="#06b6d4")) # Removed
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # self.gui_queue.put(self.redraw_canvas)

    # ===== Screen Share Toggles =====

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
            self.log("🖥 Starting screen share...", "system")
            # FIX 1: Provide immediate UI feedback
            self.screen_btn.configure(text="⏳", fg_color="#d97706", state="disabled")
            threading.Thread(target=self.ss_share_wrapper, daemon=True).start()
        else:
            self.log("🖥 Stopping screen share...", "system")
            self._stop_screen_share()
    
    # --- FIX: Refactored to prevent UI freeze ---
    def _stop_screen_share(self):
        with self.screen_share_lock:
            self.sharing_screen = False # Signal loop to stop
            self.screen_maximized = False
            self.screen_frame = None
            self.current_presenter_addr = None
        
        # GUI updates are safe
        # FIX v8.0: Redraw is handled by the canvas_updater_loop
        # self.gui_queue.put(self.redraw_canvas) # Redraw to show grid
        
        # Offload all network cleanup to a background thread to prevent freezing
        threading.Thread(target=self._stop_share_network_tasks, daemon=True).start()

    def _stop_share_network_tasks(self):
        try:
            # 1. Tell the server we are stopping
            # FIX 1: Send the present_stop message
            if self.tcp_sock:
                self.tcp_sock.sendall(pack_control({"type": "present_stop"}))
        except Exception as e:
            self.log(f"Error sending present_stop: {e}", "error")
        
        # 2. Clean up the dedicated screen sharing socket
        self._cleanup_share_socket()
    # --- END FIX ---

    # Wrapper to run the blocking call in a thread
    def ss_share_wrapper(self):
        self._start_screen_share()

    def _start_screen_share(self):
        # 1. Ensure any old socket is dead
        self._cleanup_share_socket()
        
        # 2. Give a moment for ports to free up
        time.sleep(0.5)
        
        try:
            # 3. Create new socket
            self.sharing_screen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sharing_screen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sharing_screen_sock.settimeout(15.0) # Connection timeout
            
            self.log("🖥 Connecting to screen share server...", "system")
            self.sharing_screen_sock.connect((self.server_ip, SCREEN_TCP_PORT))
            
            # 4. Send role
            # FIX 2: Send name to server for multi-share tagging
            if not write_msg(self.sharing_screen_sock, {"role": "presenter", "name": self.username}):
                raise ConnectionError("Failed to send presenter role")
            
            # 5. Wait for response
            response = read_msg(self.sharing_screen_sock)
            
            if response is None:
                raise ConnectionError("Server did not respond - timeout")
            
            if response.get("status") != "ok":
                reason = response.get("reason", "Server rejected request")
                self.gui_queue.put(lambda r=reason: messagebox.showerror("Screen Share", f"Rejected: {r}"))
                # FIX 1: Reset button on failure
                self.gui_queue.put(lambda: self.screen_btn.configure(text="📤", fg_color="#f59e0b", hover_color="#d97706", state="normal"))
                self._cleanup_share_socket()
                return
            
            # 6. Success! Set state and start loop
            with self.screen_share_lock:
                self.sharing_screen = True
            
            # FIX v7.3: Log VBG pause
            if self.vbg_state == "image":
                self.log("✨ VBG paused during screen share to improve performance.", "system")
            
            # --- FIX: ADDED PRESENTER SELF-VIEW ---
            self.screen_maximized = True
            self.current_presenter_addr = self.my_addr # We are viewing our own screen
            # --- END FIX ---
            
            self.sharing_screen_sock.settimeout(None) # Remove timeout for streaming
            
            # FIX 1: Set button to "On" state
            self.gui_queue.put(lambda: self.screen_btn.configure(text="📤", fg_color="#dc2626", hover_color="#b91c1c", state="normal"))
            self.log("✓ Screen sharing started successfully!", "success")
            
            # FIX 1: Send the present_start message over the *control* socket
            # This tells the server to broadcast "present_start" to everyone
            try:
                if self.tcp_sock:
                    self.tcp_sock.sendall(pack_control({"type": "present_start"}))
            except Exception as e:
                self.log(f"✗ Failed to send present_start: {e}", "error")
            
            self.screen_capture_loop()
        
        except Exception as e:
            self.log(f"✗ Screen share error: {e}", "error")
            self.gui_queue.put(lambda: messagebox.showerror("Screen Share", 
                f"Connection failed: {e}\n\nPlease try again."))
            # FIX 1: Reset button on exception
            self.gui_queue.put(lambda: self.screen_btn.configure(text="📤", fg_color="#f59e0b", hover_color="#d97706", state="normal"))
            self._cleanup_share_socket()
    
    def screen_capture_loop(self):
        # --- FIX: Use class attributes ---
        frame_interval = 1.0 / self.SCREEN_FPS
        
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                
                while True:
                    with self.screen_share_lock:
                        if not self.sharing_screen:
                            break # Exit loop if stopped
                    
                    start = time.time()
                    
                    try:
                        screenshot = sct.grab(monitor)
                        img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)
                        # --- FIX: Use class attributes ---
                        img = img.resize((self.SCREEN_WIDTH, self.SCREEN_HEIGHT), Image.Resampling.LANCZOS)
                        
                        # --- START PRESENTER SELF-VIEW ---
                        self.screen_frame = img
                        # FIX v8.0: Redraw is handled by the canvas_updater_loop
                        # self.gui_queue.put(self.redraw_canvas)
                        # --- END PRESENTER SELF-VIEW ---
                        
                        buf = io.BytesIO()
                        # --- FIX: Use class attributes ---
                        img.save(buf, format='JPEG', quality=self.SCREEN_QUALITY, optimize=True)
                        frame_data = buf.getvalue()
                        
                        # FIX 1: Encode as base64, not hex
                        frame_b64 = base64.b64encode(frame_data).decode('ascii')
                        
                        # --- FIX: Use class attributes ---
                        if not write_msg(self.sharing_screen_sock, {"type": "frame", "data": frame_b64,
                                                             "width": self.SCREEN_WIDTH, "height": self.SCREEN_HEIGHT}):
                            self.log("✗ Screen send failed - Connection lost", "error")
                            break
                    
                    except Exception as e:
                        self.log(f"✗ Screen frame error: {e}", "error")
                        break
                    
                    elapsed = time.time() - start
                    sleep_time = max(0, frame_interval - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
        
        except Exception as e:
            self.log(f"✗ Screen capture error: {e}", "error")
        
        # --- FIX: Removed redundant 'finally' block ---
        # The 'finally' block that called self._stop_screen_share() was
        # redundant and could cause race conditions. The stop logic
        # is already correctly handled by toggle_screen_share.
        
    
    # ===== Screen View Toggles =====

    # REMOVED: toggle_screen_view()
    
    # Wrapper to run the blocking call in a thread
    def ss_view_wrapper(self):
        self._start_screen_view()

    def _start_screen_view(self):
        # 1. Ensure any old socket is dead
        self._cleanup_view_socket()
        
        # 2. Wait a moment
        time.sleep(0.5)
        
        try:
            # 3. Create new socket
            self.viewing_screen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.viewing_screen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.viewing_screen_sock.settimeout(15.0)
            
            self.log("👁 Connecting to view screen...", "system")
            self.viewing_screen_sock.connect((self.server_ip, SCREEN_TCP_PORT))
            
            if not write_msg(self.viewing_screen_sock, {"role": "viewer", "name": self.username}): # FIX 2: Send name
                raise ConnectionError("Failed to send role")
            
            # 5. Wait for response
            response = read_msg(self.viewing_screen_sock)
            
            if response is None:
                raise ConnectionError("No response from server - Connection timeout")
            
            if response.get("status") != "ok":
                reason = response.get("reason", "Unknown error")
                self.gui_queue.put(lambda r=reason: messagebox.showerror("View Screen", r))
                self._cleanup_view_socket()
                return
            
            # 6. Success!
            with self.screen_share_lock:
                self.viewing_screen = True
                # self.screen_maximized = True # Don't maximize until we get a frame
            
            # FIX 2: Log current presenters
            presenters = response.get("presenters", [])
            self.log(f"👁 Viewing screen. Active presenters: {', '.join(presenters) if presenters else 'None'}", "success")

            self.viewing_screen_sock.settimeout(None)
            # self.gui_queue.put(lambda: self.view_screen_btn.configure(fg_color="#dc2626", hover_color="#b91c1c")) # Removed
            # self.log("👁 Viewing screen - Connected successfully", "success") # Covered by above
            
            self.screen_receive_loop()
        
        except Exception as e:
            self.log(f"✗ View failed: {e}", "error")
            self.gui_queue.put(lambda: messagebox.showerror("View Screen", f"Failed to connect: {str(e)}"))
            self._cleanup_view_socket()
    
    def screen_receive_loop(self):
        try:
            while True:
                with self.screen_share_lock:
                    if not self.viewing_screen:
                        break
                
                msg = read_msg(self.viewing_screen_sock)
                if not msg:
                    self.log("✗ Screen view disconnected", "system")
                    break
                
                mtype = msg.get("type")
                
                if mtype == "frame":
                    # FIX 2: Store who this frame is from
                    self.current_presenter_addr = msg.get("addr")
                    self.screen_maximized = True # Got a frame, now maximize
                    
                    # FIX 1: Decode from base64
                    frame_data = base64.b64decode(msg["data"])
                    img = Image.open(io.BytesIO(frame_data))
                    self.screen_frame = img
                    # FIX v8.0: Redraw is handled by the canvas_updater_loop
                    # self.gui_queue.put(self.redraw_canvas)
                
                elif mtype == "present_start":
                    # FIX 2: Handle new presenter starting
                    self.log(f"🖥 {msg.get('from')} started presenting", "system")
                    # We will automatically see their frames when they arrive
                
                elif mtype == "present_stop":
                    # FIX 2: Handle a presenter stopping
                    self.log(f"🖥 {msg.get('from')} stopped presenting", "system")
                    if msg.get("addr") == self.current_presenter_addr:
                        # The screen we were watching is gone.
                        # Go back to grid and wait for another frame.
                        self.screen_frame = None
                        self.current_presenter_addr = None
                        self.screen_maximized = False
                        # FIX v8.0: Redraw is handled by the canvas_updater_loop
                        # self.gui_queue.put(self.redraw_canvas)
        
        except Exception as e:
            if self.viewing_screen:
                self.log(f"✗ Screen receive error: {e}", "error")
        
        finally:
            # Final cleanup
            self._cleanup_view_socket()
    
    def log(self, text, msg_type="default"):
        self.gui_queue.put(lambda: self._update_log(text, msg_type))
    
    def _update_log(self, text, msg_type="default"):
        try:
            timestamp = time.strftime("%H:%M:%S")
            self.log_textbox.insert("end", f"[{timestamp}] {text}\n")
            self.log_textbox.see("end")
        except:
            pass

if __name__ == "__main__":
    # Ensure app scales well on high-DPI displays
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass # Only works on Windows
        
    root = ctk.CTk()
    app = ModernConferenceClient(root)
    root.mainloop()



"""

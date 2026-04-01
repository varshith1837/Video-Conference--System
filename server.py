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
    """Attempts to auto-detect the local IP address."""
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
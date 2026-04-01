# NetConf: Real-Time Video Conferencing System

A full-featured, real-time video conferencing application built from scratch using Python socket programming. Supports multi-user video/audio streaming, screen sharing, chat, file transfer, and AI-powered virtual backgrounds — all over a custom TCP/UDP client-server architecture.

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![Sockets](https://img.shields.io/badge/Networking-TCP%2FUDP%20Sockets-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

| Feature | Description |
|---|---|
| 🎥 **Live Video** | Real-time video streaming at 15 FPS with JPEG compression via UDP |
| 🎤 **Audio Streaming** | 16kHz audio capture, server-side mixing with packet loss concealment |
| 🖥️ **Screen Sharing** | Full-screen capture and relay with presenter/viewer architecture |
| 💬 **Chat** | Public and private (DM) messaging with timestamps |
| 📁 **File Transfer** | Upload and download files through the server |
| 😂 **Emoji Reactions** | Animated emoji reactions overlaid on video tiles |
| 🪄 **Virtual Background** | AI-powered background replacement using MediaPipe segmentation |
| 🔊 **Active Speaker** | Visual highlight on the currently speaking participant |
| 👥 **User Management** | Dynamic user list, join/leave notifications, unique username enforcement |
| 🌙 **Dark/Light Theme** | Toggle between dark and light mode |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    SERVER                           │
│                                                     │
│  TCP Control (9000)  ─── User mgmt, chat, signals  │
│  UDP Video (10000)   ─── Video frame relay          │
│  UDP Audio (11000)   ─── Audio mixing & relay       │
│  TCP Screen (9001)   ─── Screen share relay         │
│  TCP Files (9002)    ─── File upload/download       │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
        ┌──────┴──────┐        ┌──────┴──────┐
        │  CLIENT 1   │        │  CLIENT 2   │
        │  (Tkinter)  │        │  (Tkinter)  │
        └─────────────┘        └─────────────┘
```

- **TCP** for reliable control messages, file transfer, and screen sharing
- **UDP** for low-latency video and audio streaming
- **Multithreaded** server handles concurrent clients with thread-safe state management

---

## Tech Stack

- **Language:** Python 3.8+
- **Networking:** TCP/UDP Sockets, JSON message framing
- **Video:** OpenCV, Pillow
- **Audio:** PyAudio, NumPy (server-side mixing)
- **Screen Capture:** MSS
- **Virtual Background:** MediaPipe Selfie Segmentation
- **GUI:** CustomTkinter
- **Platform:** Windows (primary)

---

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Windows OS (recommended)
- Webcam and microphone

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/<your-username>/NetConf.git
   cd NetConf
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install mediapipe  # Optional: for virtual backgrounds
   ```

3. **Start the server**
   ```bash
   python server.py
   ```

4. **Launch the client** (on same or different machine)
   ```bash
   python client.py
   ```

5. Enter the server IP and a username, then click **Connect**.

---

## Project Structure

```
├── server.py           # Multithreaded server (TCP control, UDP video/audio, screen relay, file server)
├── client.py           # Full-featured GUI client with video, audio, screen share, chat
├── requirements.txt    # Python dependencies
├── icon.ico            # Application icon
├── icon.jpg            # Application logo
└── README.md
```

---

## 📸 Screenshots

> _Add screenshots of the application here_

---

## Configuration

Key parameters can be adjusted in both `server.py` and `client.py`:

| Parameter | Default | Description |
|---|---|---|
| `VIDEO_FPS` | 15 | Video capture frame rate |
| `VIDEO_WIDTH x HEIGHT` | 640 x 480 | Video resolution |
| `AUDIO_RATE` | 16000 Hz | Audio sample rate |
| `JPEG_QUALITY` | 80 | Video compression quality |
| `SCREEN_FPS` | 10 | Screen share frame rate |

---

## License

This project is licensed under the MIT License.

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to open an issue or submit a pull request.

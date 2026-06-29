# CamScan — Face Recognition System

A FastAPI-based face recognition system with AI-powered registration, live detection, and a Laravel dashboard.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Face Recognition | InsightFace (buffalo_l model) |
| AI Analysis | Mistral Pixtral vision |
| Voice Countdown | pyttsx3 (offline TTS) |
| Database | MySQL via XAMPP |
| ORM | SQLAlchemy |
| Dashboard | Laravel (separate) |

---

## Project Structure

```
CamScan/
├── main.py                        # App entry point, camera stream
├── database.py                    # DB connection, session, create_tables()
├── models.py                      # SQLAlchemy ORM models
├── requirements.txt               # Python dependencies
├── .env                           # Environment variables (never commit this)
├── alter_tables.sql               # SQL migration for new columns
│
├── routers/
│   └── registration.py            # Registration endpoints
│
├── services/
│   ├── face_service.py            # InsightFace model, embeddings, matching
│   ├── user_service.py            # User CRUD, file saving
│   ├── detection_service.py       # Detection logs, snapshots
│   ├── video_service.py           # MP4 recording, video DB logs
│   ├── capture_service.py         # Webcam #1, 3-2-1 voice countdown
│   └── mistral_service.py         # Mistral Pixtral AI vision analysis
│
├── uploads/
│   ├── faces/                     # Captured face photos
│   └── profiles/                  # Profile / ID-card photos
├── snapshots/                     # Detection snapshots
└── videos/                        # Recorded video files
```

---

## Database Tables

### `users`
| Column | Type | Description |
|---|---|---|
| id | INT | Primary key |
| name | VARCHAR(100) | Full name |
| age | SMALLINT | Estimated age (from Mistral AI) |
| gender | VARCHAR(20) | Gender (from Mistral AI) |
| face_image | VARCHAR(512) | Path to face photo |
| face_embeding | LONGTEXT | Cached embedding JSON (512 floats) |
| position | VARCHAR(100) | Job title / role |
| image_user | VARCHAR(512) | Path to profile/ID photo |
| ai_notes | VARCHAR(255) | Mistral AI photo quality notes |
| created_at | DATETIME | Registration timestamp |

### `face_embeddings`
| Column | Type | Description |
|---|---|---|
| id | INT | Primary key |
| user_id | INT | FK → users.id |
| embedding | LONGTEXT | 512-d float array as JSON |
| created_at | DATETIME | Created timestamp |

### `detections`
| Column | Type | Description |
|---|---|---|
| id | INT | Primary key |
| user_id | INT | FK → users.id (NULL = unknown) |
| confidence | VARCHAR(10) | Cosine similarity score e.g. "0.9231" |
| camera_name | VARCHAR(100) | Human-readable camera label |
| camera_id | VARCHAR(100) | Camera identifier |
| snapshot_path | VARCHAR(512) | Path to detection snapshot |
| position | VARCHAR(100) | Location/position of camera |
| detected_at | DATETIME | Detection timestamp |

### `videos`
| Column | Type | Description |
|---|---|---|
| id | INT | Primary key |
| user_id | INT | FK → users.id |
| video_path | VARCHAR(512) | Path to MP4 file |
| created_at | DATETIME | Created timestamp |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

```env
# MySQL — XAMPP default (no password)
DATABASE_URL=mysql+pymysql://root:@localhost:3306/camscan

# Mistral AI — get free key at https://console.mistral.ai
MISTRAL_API_KEY=your_mistral_api_key_here
MISTRAL_AGENT_ID=ag_019f12454e1c719eaeb6258b095471d1
MISTRAL_AGENT_VERSION=0
```

---

## Setup & Installation

### 1. Prerequisites
- Python 3.11+
- XAMPP (Apache + MySQL running)
- Webcam connected

### 2. Create virtual environment
```bash
cd /path/to/CamScan
python -m venv venv

# Mac/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set up database
- Open XAMPP Control Panel → Start **Apache** and **MySQL**
- Go to `http://localhost/phpmyadmin`
- Click **New** → name it `camscan` → click **Create**
- Click the `camscan` database → click **SQL** tab
- Paste and run:

```sql
CREATE TABLE users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(100)  NOT NULL,
    age           SMALLINT      NULL,
    gender        VARCHAR(20)   NULL,
    face_image    VARCHAR(512)  NULL,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    face_embeding LONGTEXT      NULL,
    position      VARCHAR(100)  NULL,
    image_user    VARCHAR(512)  NULL,
    ai_notes      VARCHAR(255)  NULL
);

CREATE TABLE face_embeddings (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT      NOT NULL,
    embedding  LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE detections (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT          NULL,
    confidence    VARCHAR(10)  NULL,
    camera_name   VARCHAR(100) NULL,
    camera_id     VARCHAR(100) NULL,
    snapshot_path VARCHAR(512) NULL,
    detected_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    position      VARCHAR(100) NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE videos (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT          NULL,
    video_path VARCHAR(512) NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);
```

### 5. Configure .env
```bash
cp .env.example .env
# Edit .env with your values
```

### 6. Run the server
```bash
uvicorn main:app --reload --port 8001
```

Open `http://localhost:8001/docs` — Swagger UI shows all endpoints.

---

## API Endpoints

### Registration

| Method | Endpoint | Description |
|---|---|---|
| POST | `/register/search` | Capture face and search existing users |
| POST | `/register/user/confirm` | Save captured user to DB (after /search) |
| POST | `/register/user` | Manual upload registration (also runs Mistral) |
| POST | `/register/user/{id}/face` | Add extra face photo to existing user |
| GET | `/register/users` | List all registered users |
| GET | `/register/users/embeddings` | All embeddings (used by detection engine) |
| GET | `/register/preview` | Single JPEG frame from Webcam #1 (camera check) |
| DELETE | `/register/user/{id}` | Delete user and all their data |

### Live Stream

| Method | Endpoint | Description |
|---|---|---|
| GET | `/video_feed` | MJPEG live stream from Webcam #1 |

---

## Registration Flow (Webcam)

```
1.  Call  POST /register/search
          │
          ▼
2.  Webcam speaks "3... 2... 1... Smile!"
          │
          ▼
3.  Frame captured from Webcam #1
          │
          ▼
4.  InsightFace detects face + generates 512-d embedding
          │
          ▼
5.  Server compares embedding to all stored users
          │
          ▼
6.  Response: matched user data + confidence if found, or "No matching user found."

7.  Frontend can use the matched user directly or fall back to registration.

8.  Call  POST /register/user/confirm  only when creating a new user.
```

---

## Services Overview

| Service | File | Responsibility |
|---|---|---|
| Face | `face_service.py` | Load InsightFace, generate embeddings, cosine similarity matching |
| User | `user_service.py` | User CRUD, save uploaded images to disk |
| Detection | `detection_service.py` | Log detections, save snapshots, query history |
| Video | `video_service.py` | Start/stop MP4 recording per camera, log to DB |
| Capture | `capture_service.py` | Control Webcam #1, speak 3-2-1 countdown via pyttsx3 |
| Mistral | `mistral_service.py` | Call Mistral Pixtral API, parse age/gender/position from photo |

---

## Face Matching Logic

- Model: `buffalo_l` (InsightFace) — 512-dimensional embeddings
- Similarity: cosine similarity
- Match threshold: `0.5` (adjustable in `face_service.py`)
- Multiple embeddings per user supported (different angles/lighting)
- Largest face chosen when multiple faces appear in frame

---

## Common Issues

| Error | Cause | Fix |
|---|---|---|
| `ImportError: cannot import name 'Base' from 'models'` | Python cache conflict | Run `find . -name __pycache__ -exec rm -rf {} +` then retry |
| `Access denied for user 'root'` | Wrong DB password in `.env` | XAMPP default has no password — use `root:@localhost` |
| `No face detected` | Blurry or dark photo | Better lighting, move closer to camera |
| `MISTRAL_API_KEY not set` | Missing `.env` value | Add key from console.mistral.ai to `.env` |
| `Cannot open webcam` | Camera index wrong or in use | Try `cv2.VideoCapture(1)` in `capture_service.py` |
| `pyttsx3` no sound | Audio driver issue on Mac | Run `pip install pyttsx3` and check system audio output |

---

## Getting API Keys

- **Mistral AI** — free tier available: https://console.mistral.ai
  - Model used: `pixtral-12b-2409` (vision)

---

## Notes

- `face_embeding` column name has one `d` — intentional, matches existing DB schema
- Embeddings are stored as JSON arrays in `LONGTEXT` columns — readable from Laravel without extra libraries
- `pyttsx3` works fully offline — no internet required for voice countdown
- InsightFace downloads the `buffalo_l` model (~300 MB) on first run automatically
- All uploaded files stay local — nothing is sent to external servers except the Mistral API call

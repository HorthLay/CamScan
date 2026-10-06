# CamScan API Reference

Base URL: `http://localhost:8001`
Interactive docs: `http://localhost:8001/docs` (Swagger UI)

All registration endpoints are prefixed with `/register`.

---

## Health & Streaming

### `GET /`
Health check.

**Response**
```json
{ "status": "running", "message": "CamScan Face Recognition API" }
```

### `GET /video_feed`
MJPEG live stream with face detection boxes.
Stream is downscaled to 640px and encoded at JPEG quality 60 for speed.
Sent with `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers.

**Query Params**
| Param | Type | Default | Description |
|---|---|---|---|
| `stream_url` | str | *(active camera source, else webcam #1)* | CCTV / RTSP / HTTP-MJPEG stream to forward. Falls back to the current `camera_control` source, then the local webcam. |

**Media type:** `multipart/x-mixed-replace; boundary=frame`

### `GET /uploads/*`
Serves static files (face / profile images stored under `uploads/faces` and `uploads/profiles`).

---

## Camera Control (IP-aware)

Single-camera model: only one client IP may hold the camera at a time. An IP that
explicitly stops is **blocked** until it calls `clear-stop`; an implicit WS
disconnect auto-releases the camera without blocking the IP.

### `GET /register/camera/status`
Status for the requesting IP.

**Response**
```json
{
  "success": true,
  "message": "Camera available.",
  "ip": "192.168.1.20",
  "is_allowed": true,
  "is_active": false,
  "is_stopped": false,
  "active_ips": [],
  "stopped_ips": [],
  "stream_source": ""
}
```

### `POST /register/camera/start`
Grab the camera for the requesting IP.

**Form Params**
| Param | Type | Description |
|---|---|---|
| `stream_url` | str, optional | CCTV/RTSP source to associate with this session. |

**Response:** same shape as status, `success:false` + `message` when another IP
already holds the camera or this IP is stopped.

### `POST /register/camera/stop`
Stop the camera for the requesting IP and mark it blocked until `clear-stop`.

### `POST /register/camera/clear-stop`
Remove the blocked flag for the requesting IP.

### `GET /register/camera/active-ips`
```json
{ "active_ips": ["192.168.1.20"], "stopped_ips": ["192.168.1.21"] }
```

### `GET /camera/source`
Currently active stream source (used by the Detection tab to resume a session):
```json
{ "is_active": true, "stream_source": "rtsp://192.168.0.10/stream" }
```

### `WS /ws/camera`
Real-time camera control mirroring the REST API. Client sends
`{action, request_id, ...}`; server answers `{action:"response", request_id,
success, ...}` (or `{action:"error", ...}`). Broadcasts `status_update` and
`ip_status_changed` to every connected socket so the Active Users table stays in
sync. Disconnect auto-releases the camera (no permanent block).

**Actions:** `ping`→`pong`, `start_camera` (optional `stream_url`), `stop_camera`,
`get_status`, `clear_stop`, `get_active_ips`.

---

## Detection

### `POST /detect`
Analyze one frame; accepts base64 `image_data` or a live `stream_url` (one frame
is sniffed from the stream). Returns face boxes, `matched` flag and the matching
user's details when a face is recognized.

### `POST /detect/stream/start`
Register a CCTV stream for live detection (IP-aware, same rules as camera control).

### `POST /detect/stream/stop`
Stop live detection for the requesting IP.

### `GET /detect/status`
```json
{ "success": true, "is_detecting": false, "is_allowed": true,
  "is_stopped": false, "active_ips": [], "stopped_ips": [],
  "stream_source": "" }
```

### `GET /detect/results?limit=20`
Most recent in-memory detection results (max 100).

---

## Registration

### `GET /register/countdown-audio`
Browser-playable WAV countdown cue (44100 Hz, 16-bit mono, ~4s).

**Query Params**
| Param | Type | Default | Description |
|---|---|---|---|
| — | — | — | none |

**Headers**
- `Cache-Control: public, max-age=86400, immutable`
- `X-Countdown-Duration: 4`

**Media type:** `audio/wav`

---

### `POST /register/search`
Capture a fresh frame from the webcam and search for an existing user by face embedding.

**Query Params**
| Param | Type | Default | Description |
|---|---|---|---|
| `server_countdown` | bool | `false` | If `true`, server speaks a 3-2-1 voice countdown before capturing |

**Process**
1. Capture frame (with optional server-side countdown)
2. **Anti-spoofing check** (`LIVENESS_CHECK=true`): MiniFASNet rejects photos / screens / masks (returns `success: false`)
3. Generate SCRFD/ArcFace 512-d embedding from the largest face (UniFace by default)
4. Compare against all stored embeddings (vectorized cosine similarity)
5. Return best match if confidence ≥ 0.55

**Response — matched**
```json
{
  "success": true,
  "matched": true,
  "image_base64": "<base64 jpeg>",
  "liveness": {
    "live": true,
    "live_confidence": 0.93,
    "threshold": 0.6,
    "rejected": false
  },
  "message": "User matched.",
  "user": {
    "id": 1,
    "name": "John Doe",
    "age": 30,
    "date_of_birth": "1994-05-10",
    "gender": "Male",
    "position": "Engineer",
    "face_image": "uploads/faces/1_abc.jpg",
    "image_user": "uploads/profiles/1_def.jpg",
    "ai_notes": "Name: John Doe; Age: 30; User is currently working / active",
    "note": "work",
    "confidence": 0.9231
  }
}
```

**Response — spoofed (when liveness is enabled)**
```json
{
  "success": false,
  "matched": false,
  "liveness": {
    "live": false,
    "live_confidence": 0.87,
    "threshold": 0.6,
    "rejected": true
  },
  "message": "Spoofing detected — face appears to be a photo, screen, or mask."
}
```

**Response — no match**
```json
{
  "success": true,
  "matched": false,
  "image_base64": "<base64 jpeg>",
  "message": "No matching user found."
}
```

**Errors**
- `503` — Camera not available

---

### `POST /register/user/confirm`
Save a captured + analyzed user (the client plays the countdown, then uploads the captured face).

**Form Fields**
| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✅ | Full name |
| `position` | string | ❌ | Job title / role |
| `age` | int | ❌ | Estimated age |
| `date_of_birth` | date (`YYYY-MM-DD`) | ❌ | Date of birth (auto-calculates age) |
| `gender` | string | ❌ | Male / Female / Unknown |
| `note` | string | ❌ | `walkout`, `work`, or `resign` |
| `ai_notes` | string | ❌ | AI notes (generated if empty) |
| `face_image` | file | ✅ | Face photo — jpg/png/webp, ≤ 10MB |
| `image_user` | file | ❌ | Profile / ID-card photo |

**Response — `201 Created`**
```json
{
  "success": true,
  "user_id": 1,
  "name": "John Doe",
  "age": 30,
  "date_of_birth": "1994-05-10",
  "gender": "Male",
  "position": "Engineer",
  "face_image": "uploads/faces/1_abc.jpg",
  "image_user": "uploads/profiles/1_def.jpg",
  "ai_notes": "Name: John Doe; ...",
  "note": "work",
  "message": "User registered and face embedding saved."
}
```

**Errors**
- `415` — unsupported image type
- `413` — image exceeds 10 MB
- `422` — no face detected in image

---

### `POST /register/user`
Register a user by uploading a photo manually. Also runs Mistral Pixtral vision AI to auto-fill age, gender, and photo-quality notes.

**Form Fields**
| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✅ | Full name |
| `position` | string | ❌ | Job title / role |
| `age` | int | ❌ | Age (falls back to Mistral estimate) |
| `date_of_birth` | date | ❌ | Date of birth |
| `note` | string | ❌ | `walkout`, `work`, or `resign` |
| `face_image` | file | ✅ | Face photo — jpg/png/webp, ≤ 10MB |
| `image_user` | file | ❌ | Profile / ID-card photo |

**Response — `201 Created`**
```json
{
  "success": true,
  "user_id": 1,
  "name": "John Doe",
  "age": 30,
  "date_of_birth": "1994-05-10",
  "gender": "Male",
  "position": "Engineer",
  "face_image": "uploads/faces/1_abc.jpg",
  "note": "work",
  "message": "User registered successfully."
}
```

**Errors**
- `415` / `413` — image validation
- `422` — no face detected
- `502` — Mistral API error
- `503` — `MISTRAL_API_KEY` not set

---

### `GET /register/preview`
Single live JPEG frame from Webcam #1 (quality 90). Used for a camera check.

**Media type:** `image/jpeg`
**Errors:** `503` — camera not available

---

### `POST /register/user/{user_id}/face`
Add an extra face photo to an existing user (creates a new embedding, updates primary face image).

**Path Params**
| Param | Type | Description |
|---|---|---|
| `user_id` | int | Target user |

**Form Fields**
| Field | Type | Required |
|---|---|---|
| `face_image` | file | ✅ |

**Response**
```json
{ "success": true, "user_id": 1, "embedding_id": 3 }
```

**Errors**
- `404` — user not found
- `422` — no face detected

---

### `GET /register/users`
List all registered users.
Persisted `ai_notes` are returned as-is (no per-call AI regeneration).

**Response**
```json
[
  {
    "id": 1,
    "name": "John Doe",
    "age": 30,
    "date_of_birth": "1994-05-10",
    "gender": "Male",
    "position": "Engineer",
    "face_image": "uploads/faces/1_abc.jpg",
    "image_user": "uploads/profiles/1_def.jpg",
    "face_verified": true,
    "ai_notes": "Name: John Doe; ...",
    "note": "work",
    "created_at": "2026-09-24T12:00:00"
  }
]
```

---

### `GET /register/users/by-name/{name}`
Find a single user by name (case-insensitive). Used by the Laravel dashboard to
verify faces and sync/delete without downloading the whole user list.

**Response**
```json
{
  "id": 1,
  "name": "John Doe",
  "face_verified": true,
  "age": 30,
  "date_of_birth": "1994-05-10",
  "gender": "Male",
  "position": "Engineer",
  "face_image": "uploads/faces/1_abc.jpg",
  "image_user": "uploads/profiles/1_def.jpg",
  "ai_notes": "...",
  "note": "work",
  "created_at": "2026-09-24T12:00:00"
}
```

`face_verified` is `true` whenever the user has a stored face embedding.

**Errors**
- `404` — no user with that name

---

### `GET /register/users/embeddings`
All face embeddings joined with user data. Consumed by the external detection engine.

**Response**
```json
[
  {
    "embedding_id": 1,
    "user_id": 1,
    "name": "John Doe",
    "position": "Engineer",
    "age": 30,
    "date_of_birth": "1994-05-10",
    "gender": "Male",
    "face_image": "uploads/faces/1_abc.jpg",
    "image_user": "uploads/profiles/1_def.jpg",
    "ai_notes": "...",
    "note": "work",
    "embedding": "[0.012, -0.034, ... 512 floats ...]"
  }
]
```

---

### `PUT /register/user/{user_id}`
Update user info. Accepts `application/json` or form data.

**Path Params**
| Param | Type | Description |
|---|---|---|
| `user_id` | int | Target user |

**Body / Form Fields (all optional)**
| Field | Type | Description |
|---|---|---|
| `name` | string | New full name |
| `date_of_birth` | string (`YYYY-MM-DD`) | Updates + auto-recalculates age |
| `age` | int | Manual age override |
| `note` | string | `walkout` / `work` / `resign` |
| `ai_notes` | string | Explicit notes (otherwise regenerated) |

**Behavior**
- Setting `date_of_birth` recalculates `age`
- If `ai_notes` not provided, they are regenerated from name/age/DOB/note
- Syncs changes to Laravel (`POST /api/users/sync-from-fastapi`)

**Response**
```json
{
  "success": true,
  "user_id": 1,
  "name": "John Doe",
  "date_of_birth": "1994-05-10",
  "age": 30,
  "ai_notes": "...",
  "note": "work",
  "message": "User 1 updated."
}
```

**Errors**
- `404` — user not found

---

### `DELETE /register/user/{user_id}`
Delete a user and all their data (embeddings cascade via FK).

**Path Params**
| Param | Type |
|---|---|
| `user_id` | int |

**Response**
```json
{ "success": true, "message": "User 1 deleted." }
```

**Errors**
- `404` — user not found

---

### `POST /register/sync-from-laravel`
Receive user updates from the Laravel web app. Accepts JSON or form data.

**Body / Form Fields**
| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | int | ✅ | Target user |
| `name` | string | ❌ | Full name |
| `date_of_birth` | string (`YYYY-MM-DD`) | ❌ | Updates + recalculates age |
| `age` | int | ❌ | Manual age |
| `note` | string | ❌ | `walkout` / `work` / `resign` |
| `ai_notes` | string | ❌ | Explicit notes (otherwise regenerated) |

**Response**
```json
{
  "success": true,
  "user_id": 1,
  "name": "John Doe",
  "date_of_birth": "1994-05-10",
  "age": 30,
  "ai_notes": "...",
  "note": "work",
  "message": "User 1 synced from Laravel."
}
```

**Errors**
- `400` — missing / invalid `user_id`
- `404` — user not found

---

## Validation Rule

| Image Format | Max Size | Error |
|---|---|---|
| `image/jpeg`, `image/png`, `image/webp` | 10 MB | `415` (type) / `413` (size) |

## Face Matching

- Default engine: UniFace — SCRFD detection + ArcFace mobile embedding (512-d, ONNX); InsightFace `buffalo_l` fallback
- Similarity: cosine similarity (vectorized numpy matrix multiply)
- Match threshold: `0.55` on `/register/search`
- Largest face in the frame is used
- Multiple embeddings per user supported (angles / lighting)
- Anti-spoofing: `LIVENESS_CHECK=true` rejects photos / screens / masks on search

## Status Codes

| Code | Meaning |
|---|---|
| `200` | OK |
| `201` | Created (user registered / confirmed) |
| `400` | Bad request (bad payload / user_id) |
| `404` | User not found |
| `413` | Image too large (10MB limit) |
| `415` | Unsupported image type |
| `422` | No face detected |
| `502` | Mistral / upstream API error |
| `503` | Camera or API key unavailable |
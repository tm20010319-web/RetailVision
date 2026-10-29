"""
统一配置：YOLO 模型路径、Ollama 地址、输出目录等。
替代原先不存在的 backend.config。
"""

import os
from pathlib import Path

from agent.config_values import strict_bool_env


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.getenv(name, default).split(",")
        if value.strip()
    )


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """全局配置项，可通过环境变量覆盖。"""

    # YOLO
    YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")

    # Ollama 视觉大模型
    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")
    OLLAMA_VISION_TIMEOUT_SECONDS = float(
        os.getenv("OLLAMA_VISION_TIMEOUT_SECONDS", "120")
    )
    OLLAMA_VISION_MAX_RETRIES = int(
        os.getenv("OLLAMA_VISION_MAX_RETRIES", "3")
    )

    # Browser origins allowed to read the member Demo API.
    MEMBER_API_CORS_ORIGINS = _csv_env(
        "MEMBER_API_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5184,http://127.0.0.1:5184",
    )

    # 输出目录
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")

    # 数据库（MVP 阶段使用 SQLite，生产环境通过环境变量覆盖）
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///retailvision.db")

    # Identity association is intentionally unset by default. Production
    # startup must choose exactly one mode through the StoreRuntime root.
    IDENTITY_ASSOCIATION_MODE = os.getenv("IDENTITY_ASSOCIATION_MODE")
    IDENTITY_ASSOCIATION_STORE_ID = os.getenv("IDENTITY_ASSOCIATION_STORE_ID", "")
    IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID = os.getenv(
        "IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID", ""
    )
    ASSOCIATION_WINDOW_SECONDS = float(os.getenv("ASSOCIATION_WINDOW_SECONDS", "1.0"))
    SNAPSHOT_BUFFER_SECONDS = float(os.getenv("SNAPSHOT_BUFFER_SECONDS", "5.0"))
    ASSOCIATION_DEADLINE_SECONDS = float(os.getenv("ASSOCIATION_DEADLINE_SECONDS", "5.0"))
    TRACK_LOST_SECONDS = float(os.getenv("TRACK_LOST_SECONDS", "2.0"))
    MIN_TRACK_CONFIDENCE = float(os.getenv("MIN_TRACK_CONFIDENCE", "0.55"))
    MIN_STABLE_FRAMES = int(os.getenv("MIN_STABLE_FRAMES", "3"))
    FACE_SPATIAL_MIN_CONTAINMENT = float(
        os.getenv("FACE_SPATIAL_MIN_CONTAINMENT", "0.8")
    )
    FACE_SPATIAL_UPPER_BODY_RATIO = float(
        os.getenv("FACE_SPATIAL_UPPER_BODY_RATIO", "0.45")
    )
    SNAPSHOT_BUFFER_MAX_PER_TRACK = int(os.getenv("SNAPSHOT_BUFFER_MAX_PER_TRACK", "180"))
    PRESERVE_EXISTING_RECOGNITION_ROWS = _bool_env(
        "PRESERVE_EXISTING_RECOGNITION_ROWS", "false"
    )
    INBOX_MIN_RECEIVED_AT = os.getenv("INBOX_MIN_RECEIVED_AT", "")
    IDENTITY_COMPONENT_DEBUG = _bool_env("IDENTITY_COMPONENT_DEBUG", "false")
    PHASE2B_FINALIZATION_ENABLED = strict_bool_env(
        "PHASE2B_FINALIZATION_ENABLED", "false"
    )
    PRODUCT_ANALYTICS_PROJECTION_ENABLED = strict_bool_env(
        "RETAILVISION_PRODUCT_ANALYTICS_PROJECTION_ENABLED", "false"
    )
    PRODUCT_ANALYTICS_MCP_ENABLED = strict_bool_env(
        "RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED", "false"
    )
    MEMBER_BEHAVIOR_MCP_ENABLED = strict_bool_env(
        "RETAILVISION_MEMBER_BEHAVIOR_MCP_ENABLED", "false"
    )
    PRODUCT_ANALYTICS_TRACK_HASH_SECRET = os.getenv(
        "RETAILVISION_PRODUCT_ANALYTICS_TRACK_HASH_SECRET", ""
    )
    FINALIZATION_POLL_INTERVAL_SECONDS = float(
        os.getenv("FINALIZATION_POLL_INTERVAL_SECONDS", "0.25")
    )
    FINALIZATION_CLAIM_LEASE_SECONDS = float(
        os.getenv("FINALIZATION_CLAIM_LEASE_SECONDS", "30")
    )
    FINALIZATION_RETRY_BASE_SECONDS = float(
        os.getenv("FINALIZATION_RETRY_BASE_SECONDS", "0.5")
    )
    FINALIZATION_RETRY_MAX_SECONDS = float(
        os.getenv("FINALIZATION_RETRY_MAX_SECONDS", "30")
    )
    BINDING_HEARTBEAT_INTERVAL_SECONDS = float(
        os.getenv(
            "BINDING_HEARTBEAT_INTERVAL_SECONDS",
            str(min(1.0, TRACK_LOST_SECONDS / 2)),
        )
    )
    VISIT_ACTIVITY_REFRESH_INTERVAL_SECONDS = float(
        os.getenv("VISIT_ACTIVITY_REFRESH_INTERVAL_SECONDS", "5.0")
    )

    # Camera built-in face recognition API. Credentials and the vendor
    # uniqueCode are intentionally read only from the environment.
    CAMERA_FACE_DEVICE_ID = os.getenv("CAMERA_FACE_DEVICE_ID", "")
    CAMERA_FACE_BASE_URL = os.getenv("CAMERA_FACE_BASE_URL", "")
    CAMERA_FACE_USERNAME = os.getenv("CAMERA_FACE_USERNAME", "")
    CAMERA_FACE_PASSWORD = os.getenv("CAMERA_FACE_PASSWORD", "")
    CAMERA_FACE_UNIQUE_CODE = os.getenv("CAMERA_FACE_UNIQUE_CODE", "")
    P6S_DEVICE_SERIAL = os.getenv("P6S_DEVICE_SERIAL", "")
    P6S_SIMILARITY_THRESHOLD = float(
        os.getenv("P6S_SIMILARITY_THRESHOLD", "0.75")
    )
    P6S_MAX_CAPTURE_BYTES = int(os.getenv("P6S_MAX_CAPTURE_BYTES", "1048576"))
    P6S_MAX_CAPTURE_PIXELS = int(
        os.getenv("P6S_MAX_CAPTURE_PIXELS", "12000000")
    )
    CAMERA_FACE_CONNECT_TIMEOUT = float(
        os.getenv("CAMERA_FACE_CONNECT_TIMEOUT", "2.0")
    )
    CAMERA_FACE_READ_TIMEOUT = float(os.getenv("CAMERA_FACE_READ_TIMEOUT", "5.0"))
    CAMERA_FACE_POLL_INTERVAL = float(os.getenv("CAMERA_FACE_POLL_INTERVAL", "30.0"))
    DEVICE_EVENT_WORKER_INTERVAL = float(
        os.getenv("DEVICE_EVENT_WORKER_INTERVAL", "0.25")
    )
    DEVICE_EVENT_PROCESSING_TIMEOUT = float(
        os.getenv("DEVICE_EVENT_PROCESSING_TIMEOUT", "300.0")
    )
    DEVICE_HEALTH_FRESHNESS_SECONDS = float(
        os.getenv("DEVICE_HEALTH_FRESHNESS_SECONDS", "90.0")
    )
    ENTRY_POLICY_ALLOW_RECOGNITION_FALLBACK = (
        os.getenv("ENTRY_POLICY_ALLOW_RECOGNITION_FALLBACK", "false").lower()
        == "true"
    )

    VIDEO_SOURCE = os.getenv("VIDEO_SOURCE", "0")
    PROFILE_MODE = os.getenv("PROFILE_MODE", "mock").lower()
    PROFILE_JOB_POLL_INTERVAL = float(os.getenv("PROFILE_JOB_POLL_INTERVAL", "1.0"))
    PROFILE_JOB_MAX_ATTEMPTS = int(os.getenv("PROFILE_JOB_MAX_ATTEMPTS", "5"))
    MEMBER_ENTRY_RECOVERY_INTERVAL = float(os.getenv("MEMBER_ENTRY_RECOVERY_INTERVAL", "1.0"))
    TRACK_SNAPSHOT_DIR = os.getenv(
        "TRACK_SNAPSHOT_DIR", os.path.join(OUTPUT_DIR, "member_track_snapshots")
    )
    P6S_CAPTURE_DIR = os.getenv(
        "P6S_CAPTURE_DIR", os.path.join(TRACK_SNAPSHOT_DIR, "device_push")
    )
    TRACK_SNAPSHOT_TTL_SECONDS = float(os.getenv("TRACK_SNAPSHOT_TTL_SECONDS", "600"))

    # Week-one behavior and evidence-video pilot.
    BEHAVIOR_PROJECT_ROOT = os.getenv(
        "BEHAVIOR_PROJECT_ROOT", str(Path(__file__).resolve().parents[1])
    )
    BEHAVIOR_VEST_MODEL_PATH = os.getenv("BEHAVIOR_VEST_MODEL_PATH", "")
    BEHAVIOR_LEGACY_MODEL_CONFIG = bool(BEHAVIOR_VEST_MODEL_PATH) and not (
        os.getenv("BEHAVIOR_PERSON_MODEL_PATH")
        or os.getenv("BEHAVIOR_STAFF_VEST_MODEL_PATH")
    )
    BEHAVIOR_PERSON_MODEL_PATH = os.getenv(
        "BEHAVIOR_PERSON_MODEL_PATH",
        BEHAVIOR_VEST_MODEL_PATH or "outputs/models/yolo11n.pt",
    )
    BEHAVIOR_STAFF_VEST_MODEL_PATH = os.getenv(
        "BEHAVIOR_STAFF_VEST_MODEL_PATH",
        BEHAVIOR_VEST_MODEL_PATH
        or "runs/staff_vest/baseline_v1/weights/best.pt",
    )
    BEHAVIOR_POSE_MODEL_PATH = os.getenv(
        "BEHAVIOR_POSE_MODEL_PATH", "outputs/models/yolo11n-pose.pt"
    )
    BEHAVIOR_TRACKER = os.getenv("BEHAVIOR_TRACKER", "bytetrack.yaml")
    BEHAVIOR_DEVICE = os.getenv("BEHAVIOR_DEVICE", "cuda:0")
    BEHAVIOR_CONFIDENCE = float(os.getenv("BEHAVIOR_CONFIDENCE", "0.5"))
    BEHAVIOR_STAFF_VEST_ENABLED = _bool_env(
        "BEHAVIOR_STAFF_VEST_ENABLED", "true"
    )
    BEHAVIOR_STAFF_VEST_CONFIDENCE = float(
        os.getenv("BEHAVIOR_STAFF_VEST_CONFIDENCE", "0.68")
    )
    BEHAVIOR_ROLE_WINDOW_FRAMES = int(
        os.getenv("BEHAVIOR_ROLE_WINDOW_FRAMES", "5")
    )
    BEHAVIOR_STAFF_HIT_FRAMES = int(
        os.getenv("BEHAVIOR_STAFF_HIT_FRAMES", "3")
    )
    BEHAVIOR_MAXIMUM_MISSING_FRAMES = int(
        os.getenv("BEHAVIOR_MAXIMUM_MISSING_FRAMES", "8")
    )
    # Single-camera demo attribution. This is intentionally distinct from a
    # verified Staff Identity and will be replaced by staff face recognition.
    BEHAVIOR_GREETING_MOCK_STAFF_NAME = os.getenv(
        "BEHAVIOR_GREETING_MOCK_STAFF_NAME", "演示店员"
    )
    BEHAVIOR_GREETING_V2_ENABLED = _bool_env(
        "BEHAVIOR_GREETING_V2_ENABLED", "true"
    )
    BEHAVIOR_REALTIME_GREETING_ENABLED = _bool_env(
        "BEHAVIOR_REALTIME_GREETING_ENABLED", "false"
    )
    BEHAVIOR_CONFIG_PATH = os.getenv("BEHAVIOR_CONFIG_PATH", "")
    PEAK_ENTRY_CAMERA_ID = os.getenv(
        "PEAK_ENTRY_CAMERA_ID", CAMERA_FACE_DEVICE_ID
    )
    EVIDENCE_CLIP_DIR = os.getenv(
        "EVIDENCE_CLIP_DIR", os.path.join(OUTPUT_DIR, "evidence_clips")
    )
    FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
    EVIDENCE_AUTO_PROCESS = os.getenv(
        "EVIDENCE_AUTO_PROCESS", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}

    # Device face group configuration. The group UUID is obtained from
    # ``scripts/init_face_group.py`` (or an existing device group) and must
    # be set before member face registration can work.
    CAMERA_FACE_GROUP_UUID = os.getenv("CAMERA_FACE_GROUP_UUID", "")
    CAMERA_FACE_GROUP_ID = int(os.getenv("CAMERA_FACE_GROUP_ID", "-1"))
    CAMERA_FACE_GROUP_NAME = os.getenv("CAMERA_FACE_GROUP_NAME", "retail_members")


settings = Settings()

# src/capture/__init__.py
from .video_thread import VideoThread
from .camera_source import detect_camera_source, resolve_camera_source

__all__ = ["VideoThread", "detect_camera_source", "resolve_camera_source"]

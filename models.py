from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name          = Column(String(100), nullable=False)
    face_image    = Column(String(512), nullable=True)   # path to primary face photo
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    face_embeding = Column(Text, nullable=True)           # JSON cache of latest embedding
    position      = Column(String(100), nullable=True)
    image_user    = Column(String(512), nullable=True)   # profile / ID-card image path

    embeddings = relationship("FaceEmbedding", back_populates="user", cascade="all, delete-orphan")
    detections = relationship("Detection",     back_populates="user")
    videos     = relationship("Video",         back_populates="user")


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    id         = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    embedding  = Column(Text, nullable=False)   # JSON array of 512 floats
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="embeddings")


class Detection(Base):
    __tablename__ = "detections"

    id            = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    confidence    = Column(String(10), nullable=True)    # e.g. "0.9231"
    camera_name   = Column(String(100), nullable=True)
    camera_id     = Column(String(100), nullable=True)
    snapshot_path = Column(String(512), nullable=True)
    detected_at   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    position      = Column(String(100), nullable=True)

    user = relationship("User", back_populates="detections")


class Video(Base):
    __tablename__ = "videos"

    id         = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    video_path = Column(String(512), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="videos")
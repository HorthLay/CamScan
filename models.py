from datetime import datetime, date
from sqlalchemy import Column, Integer, SmallInteger, String, Text, DateTime, ForeignKey, Date
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def calculate_age(dob: date) -> int:
    """Calculate age from date of birth."""
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name          = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=True)          # date of birth for age calculation
    age           = Column(SmallInteger, nullable=True)  # auto-calculated from date_of_birth
    gender        = Column(String(20), nullable=True)
    face_image    = Column(String(512), nullable=True)   # path to primary face photo
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    face_embeding = Column(Text, nullable=True)           # JSON cache of latest embedding
    position      = Column(String(100), nullable=True)
    image_user    = Column(String(512), nullable=True)   # profile / ID-card image path
    ai_notes      = Column(String(255), nullable=True)
    note          = Column(String(20), nullable=True)    # enum-like: walkout, work, resign

    embeddings = relationship("FaceEmbedding", back_populates="user", cascade="all, delete-orphan")
    detections = relationship("Detection",     back_populates="user")
    videos     = relationship("Video",         back_populates="user")

    @property
    def calculated_age(self) -> int:
        """Calculate age from date_of_birth property."""
        if self.date_of_birth:
            return calculate_age(self.date_of_birth)
        return None

    def update_age_from_dob(self):
        """Update age field based on date_of_birth."""
        if self.date_of_birth:
            self.age = calculate_age(self.date_of_birth)


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

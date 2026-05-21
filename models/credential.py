from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, JSON

from models.conversation import Base, utcnow


class Credential(Base):
    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    site_url = Column(String(2000), nullable=False)
    username = Column(String(500), nullable=False)
    password = Column(String(500), nullable=False)
    totp_secret = Column(String(500), nullable=True)
    selectors = Column(JSON, nullable=True)
    last_used = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    def to_dict(self, include_secrets: bool = False):
        d = {
            "id": self.id,
            "name": self.name,
            "site_url": self.site_url,
            "username": self.username,
            "totp_secret": bool(self.totp_secret),
            "selectors": self.selectors,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_secrets:
            d["password"] = self.password
            d["totp_secret"] = self.totp_secret
        return d

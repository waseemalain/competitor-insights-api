from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    plan = Column(String, default="starter")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    business_name = Column(String)
    address = Column(String)
    place_id = Column(String)
    lat = Column(String)
    lng = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AnalysisResult(Base):

    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    place_id = Column(String)

    business_name = Column(String)

    competitors_1_mile = Column(Integer)

    competitors_3_mile = Column(Integer)

    competitors_5_mile = Column(Integer)

    population = Column(Integer)

    median_income = Column(Integer)

    median_age = Column(String)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

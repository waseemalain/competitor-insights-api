from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from database import engine, SessionLocal
from models import Base, User, Location, AnalysisResult
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from typing import List
import requests
import os
import time

app = FastAPI()

# Create tables in Neon DB
Base.metadata.create_all(bind=engine)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ---------------- PASSWORD HELPERS ----------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ---------------- GOOGLE API HELPERS ----------------

def get_client_info(business_name, address):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": f"{business_name} {address}", "key": GOOGLE_API_KEY}
    response = requests.get(url, params=params).json()
    if not response.get("results"):
        return None
    res = response["results"][0]
    return {
        "place_id": res.get("place_id"),
        "name": res.get("name"),
        "lat": res["geometry"]["location"]["lat"],
        "lng": res["geometry"]["location"]["lng"]
    }

def get_nearby_scan(lat, lng, radius_meters, place_types: List[str], client_place_id):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    unique_competitors = {}

    for p_type in place_types:
        # We strip '*' if it was passed from your text list
        clean_type = p_type.strip().replace("*", "")
        params = {
            "location": f"{lat},{lng}",
            "radius": radius_meters,
            "type": clean_type,
            "key": GOOGLE_API_KEY
        }
        
        response = requests.get(url, params=params).json()
        results = response.get("results", [])

        for place in results:
            p_id = place.get("place_id")
            if p_id != client_place_id and p_id not in unique_competitors:
                # Basic quality filter: ignore places with almost no reviews
                if place.get("user_ratings_total", 0) >= 5:
                    unique_competitors[p_id] = {
                        "name": place.get("name"),
                        "rating": place.get("rating"),
                        "reviews": place.get("user_ratings_total"),
                        "address": place.get("vicinity"),
                        "place_id": p_id
                    }
    
    return list(unique_competitors.values())

# ---------------- ENDPOINTS ----------------

@app.get("/")
def root():
    return {"status": "Pali Analytics API Running"}

@app.get("/competitors")
def competitors(
    business_name: str, 
    address: str, 
    selected_types: List[str] = Query(..., description="The sub-categories from your dropdowns")
):
    # 1. Get Client Geolocation
    client = get_client_info(business_name, address)
    if not client:
        raise HTTPException(status_code=404, detail="Client business not found")

    # 2. Run Scans for 1, 3, and 5 miles
    # Conversion: 1mi = 1609m, 3mi = 4828m, 5mi = 8046m
    radius1 = get_nearby_scan(client["lat"], client["lng"], 1609, selected_types, client["place_id"])
    radius3 = get_nearby_scan(client["lat"], client["lng"], 4828, selected_types, client["place_id"])
    radius5 = get_nearby_scan(client["lat"], client["lng"], 8046, selected_types, client["place_id"])

    # 3. Save to Database
    db: Session = SessionLocal()
    try:
        new_analysis = AnalysisResult(
            user_id=1, # Default for MVP, change when Auth is fully linked
            place_id=client["place_id"],
            business_name=client["name"],
            competitors_1_mile=len(radius1),
            competitors_3_mile=len(radius3),
            competitors_5_mile=len(radius5),
            # Market data (Census) can be added here in next phase
        )
        db.add(new_analysis)
        db.commit()
    finally:
        db.close()

    return {
        "client_name": client["name"],
        "searched_types": selected_types,
        "summary": {
            "1_mile_count": len(radius1),
            "3_mile_count": len(radius3),
            "5_mile_count": len(radius5)
        },
        "results": {
            "radius_1_mile": radius1,
            "radius_3_mile": radius3,
            "radius_5_mile": radius5
        }
    }

# (Add Signup/Login endpoints here following the same db logic)

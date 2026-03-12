from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from database import engine, SessionLocal
from models import Base, User, Location, AnalysisResult
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from typing import List, Optional
import requests
import os
import time

app = FastAPI()

Base.metadata.create_all(bind=engine)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

CENSUS_API = "https://api.census.gov/data/2022/acs/acs5"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------- PASSWORD HELPERS ----------------

def hash_password(password: str):
    pwd_bytes = str(password).encode("utf-8")
    if len(pwd_bytes) > 72:
        pwd_bytes = pwd_bytes[:72]
    return pwd_context.hash(pwd_bytes.decode("utf-8", errors="ignore"))


def verify_password(plain_password, hashed_password):
    pwd_bytes = str(plain_password).encode("utf-8")
    if len(pwd_bytes) > 72:
        pwd_bytes = pwd_bytes[:72]
    return pwd_context.verify(pwd_bytes.decode("utf-8", errors="ignore"), hashed_password)


# ---------------- TOKEN CREATION ----------------

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# ---------------- ROOT ----------------

@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}


# ---------------- CLIENT INFO ----------------

def get_client_info(business_name, address):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": f"{business_name} {address}",
        "key": GOOGLE_API_KEY
    }
    response = requests.get(url, params=params).json()

    if not response.get("results"):
        return None

    result = response["results"][0]
    return {
        "place_id": result.get("place_id"),
        "name": result.get("name"),
        "lat": result["geometry"]["location"]["lat"],
        "lng": result["geometry"]["location"]["lng"],
        "rating": result.get("rating"),
        "reviews": result.get("user_ratings_total")
    }


# ---------------- MARKET DATA ----------------

def get_market_data(lat, lng):
    try:
        geo_url = f"https://geocoding.geo.census.gov/geocoder/geographies/coordinates?x={lng}&y={lat}&benchmark=Public_AR_Current&vintage=Current_Current&format=json"
        geo = requests.get(geo_url).json()
        tract = geo["result"]["geographies"]["Census Tracts"][0]
        state, county, tract_code = tract["STATE"], tract["COUNTY"], tract["TRACT"]

        params = {
            "get": "B01003_001E,B19013_001E,B01002_001E",
            "for": f"tract:{tract_code}",
            "in": f"state:{state} county:{county}"
        }
        response = requests.get(CENSUS_API, params=params).json()
        data = response[1]

        return {
            "population": int(data[0]),
            "median_income": int(data[1]),
            "median_age": float(data[2])
        }
    except Exception:
        return {"population": None, "median_income": None, "median_age": None}


# ---------------- GOOGLE NEARBY SEARCH ----------------

def get_nearby(lat, lng, radius, place_types: List[str], client_place_id):
    """
    radius: in meters
    place_types: list of Google Place Types to search for
    """
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    competitors = []

    # Since Nearby Search only allows ONE type per request, we loop through the selected sub-categories
    for p_type in place_types:
        params = {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": p_type,
            "key": GOOGLE_API_KEY
        }

        response = requests.get(url, params=params).json()
        results = response.get("results", [])

        # Simple pagination for each type
        if "next_page_token" in response:
            time.sleep(2)
            params = {"pagetoken": response["next_page_token"], "key": GOOGLE_API_KEY}
            response_next = requests.get(url, params=params).json()
            results.extend(response_next.get("results", []))

        for place in results:
            # Avoid duplicates if multiple types return the same place
            if any(c['place_id'] == place.get("place_id") for c in competitors):
                continue
            if place.get("place_id") == client_place_id:
                continue
            if place.get("user_ratings_total", 0) < 5:
                continue

            competitors.append({
                "name": place.get("name"),
                "rating": place.get("rating"),
                "reviews": place.get("user_ratings_total"),
                "address": place.get("vicinity"),
                "place_id": place.get("place_id")
            })

    return competitors


# ---------------- COMPETITOR ENDPOINT ----------------

@app.get("/competitors")
def competitors(
    business_name: str, 
    address: str, 
    selected_types: List[str] = Query(..., description="List of sub-category types selected from dropdown")
):
    """
    Example usage: /competitors?business_name=Billy+Bricks&address=Lombard&selected_types=pizza_restaurant&selected_types=italian_restaurant
    """
    client = get_client_info(business_name, address)
    if not client:
        return {"error": "Business not found"}

    market = get_market_data(client["lat"], client["lng"])

    # Perform scans at 1, 3, and 5 miles
    radius1 = get_nearby(client["lat"], client["lng"], 1609, selected_types, client["place_id"])
    radius3 = get_nearby(client["lat"], client["lng"], 4828, selected_types, client["place_id"])
    radius5 = get_nearby(client["lat"], client["lng"], 8046, selected_types, client["place_id"])

    # DB Persistence
    db: Session = SessionLocal()
    analysis = AnalysisResult(
        user_id=1,
        place_id=client["place_id"],
        business_name=client["name"],
        competitors_1_mile=len(radius1),
        competitors_3_mile=len(radius3),
        competitors_5_mile=len(radius5),
        population=market.get("population"),
        median_income=market.get("median_income"),
        median_age=market.get("median_age")
    )
    db.add(analysis)
    db.commit()
    db.close()

    return {
        "client": {
            "name": client["name"],
            "rating": client["rating"],
            "reviews": client["reviews"]
        },
        "market_data": market,
        "summary": {
            "competitors_1_mile": len(radius1),
            "competitors_3_mile": len(radius3),
            "competitors_5_mile": len(radius5)
        },
        "business_types_searched": selected_types,
        "radius_1_mile": radius1,
        "radius_3_mile": radius3,
        "radius_5_mile": radius5
    }


# ---------------- ANALYSIS HISTORY ----------------

@app.get("/analysis-history")
def analysis_history():
    db: Session = SessionLocal()
    results = db.query(AnalysisResult).order_by(AnalysisResult.created_at.desc()).all()
    data = []
    for r in results:
        data.append({
            "business_name": r.business_name,
            "competitors_1_mile": r.competitors_1_mile,
            "competitors_3_mile": r.competitors_3_mile,
            "competitors_5_mile": r.competitors_5_mile,
            "population": r.population,
            "median_income": r.median_income,
            "median_age": r.median_age,
            "created_at": r.created_at
        })
    db.close()
    return data


# ---------------- MODELS ----------------

class SignupRequest(BaseModel):
    email: str
    password: str
    business_name: str
    address: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------- SIGNUP / LOGIN ----------------

@app.post("/signup")
def signup(data: SignupRequest):
    db: Session = SessionLocal()
    try:
        email_clean = data.email.lower().strip()
        existing = db.query(User).filter(User.email == email_clean).first()
        if existing:
            return {"error": "User already exists"}

        client = get_client_info(data.business_name, data.address)
        if not client:
            return {"error": "Business not found"}

        new_user = User(
            email=email_clean,
            password_hash=hash_password(data.password),
            plan="starter",
            created_at=datetime.utcnow()
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        location = Location(
            user_id=new_user.id,
            business_name=client["name"],
            address=data.address,
            place_id=client["place_id"],
            lat=client["lat"],
            lng=client["lng"]
        )
        db.add(location)
        db.commit()

        return {"status": "account_created", "business": client["name"]}
    finally:
        db.close()


@app.post("/login")
def login(data: LoginRequest):
    db: Session = SessionLocal()
    try:
        email_clean = data.email.lower().strip()
        user = db.query(User).filter(User.email == email_clean).first()
        if not user or not verify_password(data.password, user.password_hash):
            return {"error": "Invalid credentials"}
        return {"access_token": create_access_token(data={"user_id": user.id}), "token_type": "bearer"}
    finally:
        db.close()

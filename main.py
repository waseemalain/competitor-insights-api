from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from database import engine, SessionLocal
from models import Base, User, Location
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
import requests
import os
import time

app = FastAPI()

# Create tables on startup
Base.metadata.create_all(bind=engine)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Initialize password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------- PASSWORD HELPERS ----------------

def hash_password(password: str):
    # Bcrypt has a 72-byte limit. Truncating prevents the 500 ValueError.
    return pwd_context.hash(str(password)[:72])


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(str(plain_password)[:72], hashed_password)


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


# ---------------- VALIDATE BUSINESS ----------------

@app.get("/validate-business")
def validate_business(business_name: str, address: str):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": f"{business_name} {address}",
        "key": GOOGLE_API_KEY
    }

    response = requests.get(url, params=params).json()
    results = response.get("results", [])

    if not results:
        return {"error": "Business not found. Please use the exact business name from Google Maps."}

    top_results = []
    for r in results[:3]:
        top_results.append({
            "name": r.get("name"),
            "address": r.get("formatted_address"),
            "rating": r.get("rating"),
            "reviews": r.get("user_ratings_total"),
            "place_id": r.get("place_id")
        })

    return {
        "matches_found": len(top_results),
        "results": top_results
    }


# ---------------- CLIENT INFO HELPER ----------------

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
        "types": result.get("types", []),
        "rating": result.get("rating"),
        "reviews": result.get("user_ratings_total")
    }


# ---------------- COMPETITOR LOGIC ----------------

def miles_to_meters(miles):
    return int(miles * 1609.34)


def infer_keyword(name, types):
    name = name.lower()
    if "pizza" in name: return "pizza"
    if "dentist" in types: return "dentist"
    if "plumber" in types: return "plumber"
    if "beauty_salon" in types: return "salon"
    if "gym" in types: return "gym"
    return None


def get_nearby(lat, lng, radius, keyword, client_place_id):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "keyword": keyword,
        "key": GOOGLE_API_KEY
    }

    response = requests.get(url, params=params).json()
    results = response.get("results", [])

    # Handle pagination for more results
    while "next_page_token" in response:
        time.sleep(2)  # Google requires a short delay before the token becomes valid
        params = {"pagetoken": response["next_page_token"], "key": GOOGLE_API_KEY}
        response = requests.get(url, params=params).json()
        results.extend(response.get("results", []))

    competitors = []
    for place in results:
        if place.get("place_id") == client_place_id:
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
def competitors(business_name: str, address: str):
    client = get_client_info(business_name, address)
    if not client:
        return {"error": "Business not found"}

    keyword = infer_keyword(client["name"], client["types"])

    return {
        "client": {
            "name": client["name"],
            "rating": client["rating"],
            "reviews": client["reviews"]
        },
        "keyword_detected": keyword,
        "radius_1_mile": get_nearby(client["lat"], client["lng"], miles_to_meters(1), keyword, client["place_id"]),
        "radius_3_mile": get_nearby(client["lat"], client["lng"], miles_to_meters(3), keyword, client["place_id"]),
        "radius_5_mile": get_nearby(client["lat"], client["lng"], miles_to_meters(5), keyword, client["place_id"])
    }


# ---------------- MODELS ----------------

class SignupRequest(BaseModel):
    email: str
    password: str
    business_name: str
    address: str

class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------- SIGNUP ENDPOINT ----------------

@app.post("/signup")
def signup(data: SignupRequest):
    db: Session = SessionLocal()
    try:
        # Normalize email
        email_clean = data.email.lower().strip()
        
        existing = db.query(User).filter(User.email == email_clean).first()
        if existing:
            return {"error": "User already exists"}

        client = get_client_info(data.business_name, data.address)
        if not client:
            return {"error": "Business not found. Please use your exact Google Maps business name."}

        # 1. Create User
        new_user = User(
            email=email_clean,
            password_hash=hash_password(data.password),
            plan="starter",
            created_at=datetime.utcnow()
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        # 2. Link Location
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

        return {
            "status": "account_created",
            "business": client["name"],
            "place_id": client["place_id"]
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------- LOGIN ENDPOINT ----------------

@app.post("/login")
def login(data: LoginRequest):
    db: Session = SessionLocal()
    try:
        email_clean = data.email.lower().strip()
        user = db.query(User).filter(User.email == email_clean).first()

        if not user or not verify_password(data.password, user.password_hash):
            return {"error": "Invalid credentials"}

        access_token = create_access_token(data={"user_id": user.id})
        return {"access_token": access_token, "token_type": "bearer"}
    finally:
        db.close()

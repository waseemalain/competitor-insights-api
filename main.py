from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from database import engine, SessionLocal
from models import Base, User, Location
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
import requests
import os
import time
import urllib.parse

CENSUS_API = "https://api.census.gov/data/2022/acs/acs5"

app = FastAPI()

# Create tables on startup
Base.metadata.create_all(bind=engine)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Initialize password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------- MODELS ----------------

class SignupRequest(BaseModel):
    email: str
    password: str
    business_name: str
    address: str

class LoginRequest(BaseModel):
    email: str
    password: str

# ---------------- HELPERS ----------------

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

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def generate_multi_marker_url(client_address, competitors):
    # This generates a Google Maps route URL which pins multiple locations
    base_url = "https://www.google.com/maps/dir/"
    locations = [urllib.parse.quote(client_address)]
    for comp in competitors[:9]: # Max 10 stops
        locations.append(urllib.parse.quote(comp['address']))
    return base_url + "/".join(locations)

def get_market_data(lat, lng):
    try:
        geo_url = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lng}&format=json"
        geo = requests.get(geo_url, timeout=5).json()
        state, county = geo["State"]["FIPS"], geo["County"]["FIPS"]
        tract = geo["Block"]["FIPS"][5:11]

        params = {
            "get": "B01003_001E,B19013_001E,B01002_001E",
            "for": f"tract:{tract}",
            "in": f"state:{state} county:{county}"
        }
        census_res = requests.get(CENSUS_API, params=params, timeout=5)
        if census_res.status_code != 200:
            return {"population": "N/A", "median_income": "N/A", "median_age": "N/A"}
        
        data = census_res.json()[1]
        return {
            "population": data[0], 
            "median_income": f"${int(data[1]):,}" if data[1] and int(data[1]) > 0 else "N/A", 
            "median_age": data[2]
        }
    except:
        return {"population": "Error", "median_income": "Error", "median_age": "Error"}

def get_client_info(business_name, address):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": f"{business_name} {address}", "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params).json()
    if not res.get("results"): return None
    result = res["results"][0]
    return {
        "place_id": result.get("place_id"),
        "name": result.get("name"),
        "lat": result["geometry"]["location"]["lat"],
        "lng": result["geometry"]["location"]["lng"],
        "types": result.get("types", []),
        "rating": result.get("rating"),
        "reviews": result.get("user_ratings_total")
    }

def get_nearby(lat, lng, radius, keyword, client_place_id):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {"location": f"{lat},{lng}", "radius": radius, "keyword": keyword, "key": GOOGLE_API_KEY}
    response = requests.get(url, params=params).json()
    raw_results = response.get("results", [])
    
    return [{
        "name": p.get("name"),
        "rating": p.get("rating"),
        "reviews": p.get("user_ratings_total"),
        "address": p.get("vicinity"),
        "place_id": p.get("place_id")
    } for p in raw_results if p.get("place_id") != client_place_id]

# ---------------- ENDPOINTS ----------------

@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}

@app.get("/competitors")
def competitors_endpoint(business_name: str, address: str):
    client = get_client_info(business_name, address)
    if not client:
        raise HTTPException(status_code=404, detail="Business not found")

    keyword = "pizza" if "pizza" in client["name"].lower() else "restaurant" 
    market = get_market_data(client["lat"], client["lng"])
    
    # 1609 meters = 1 mile
    r1 = get_nearby(client["lat"], client["lng"], 1609, keyword, client["place_id"])
    r3 = get_nearby(client["lat"], client["lng"], 4828, keyword, client["place_id"])
    r5 = get_nearby(client["lat"], client["lng"], 8046, keyword, client["place_id"])

    map_link = generate_multi_marker_url(address, r1)

    return {
        "client": {"name": client["name"], "rating": client["rating"], "reviews": client["reviews"]},
        "market_data": market,
        "map_view": map_link,
        "radius_1_mile": r1,
        "radius_3_mile": r3,
        "radius_5_mile": r5
    }

@app.post("/signup")
def signup(data: SignupRequest):
    db: Session = SessionLocal()
    try:
        email_clean = data.email.lower().strip()
        existing = db.query(User).filter(User.email == email_clean).first()
        if existing: return {"error": "User already exists"}

        client = get_client_info(data.business_name, data.address)
        if not client: return {"error": "Business not found on Google Maps."}

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
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
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

        if not SECRET_KEY:
             raise HTTPException(status_code=500, detail="Missing SECRET_KEY in Render settings")

        access_token = create_access_token(data={"user_id": user.id})
        return {"access_token": access_token, "token_type": "bearer"}
    finally:
        db.close()

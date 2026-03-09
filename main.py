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

def generate_multi_marker_url(client_address, competitors):
    # Google Maps Search URL with markers (more modern approach)
    base_url = "https://www.google.com/maps/dir/"
    locations = [urllib.parse.quote(client_address)]
    # Add first 5 competitors as stops (URLs have limits)
    for comp in competitors[:5]:
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
        return {"population": data[0], "median_income": data[1], "median_age": data[2]}
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
    results = []
    response = requests.get(url, params=params).json()
    results.extend(response.get("results", []))
    
    # We only take top results to keep it fast
    return [{
        "name": p.get("name"),
        "rating": p.get("rating"),
        "reviews": p.get("user_ratings_total"),
        "address": p.get("vicinity"),
        "place_id": p.get("place_id")
    } for p in results if p.get("place_id") != client_place_id]

# ---------------- ENDPOINTS ----------------

@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}

@app.get("/competitors")
def competitors_endpoint(business_name: str, address: str):
    client = get_client_info(business_name, address)
    if not client:
        raise HTTPException(status_code=404, detail="Business not found")

    keyword = "pizza" if "pizza" in client["name"].lower() else "restaurant" # simplified for demo
    
    market = get_market_data(client["lat"], client["lng"])
    
    r1 = get_nearby(client["lat"], client["lng"], 1609, keyword, client["place_id"])
    r3 = get_nearby(client["lat"], client["lng"], 4828, keyword, client["place_id"])
    r5 = get_nearby(client["lat"], client["lng"], 8046, keyword, client["place_id"])

    # Generate the map link using the closest competitors
    map_link = generate_multi_marker_url(address, r1)

    return {
        "client": {"name": client["name"], "rating": client["rating"], "reviews": client["reviews"]},
        "market_data": market,
        "map_view": map_link,
        "radius_1_mile": r1,
        "radius_3_mile": r3,
        "radius_5_mile": r5
    }

# (Keep your SignupRequest, LoginRequest, signup, and login endpoints as they were)

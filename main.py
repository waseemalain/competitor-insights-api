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


# ---------------- PASSWORD HELPERS ----------------


def hash_password(password: str):
    # Force to string, encode to bytes to check actual byte length, then truncate
    pwd_bytes = str(password).encode("utf-8")
    if len(pwd_bytes) > 72:
        pwd_bytes = pwd_bytes[:72]
    
    # Passlib needs a string, so we decode back after ensuring byte-length is safe
    return pwd_context.hash(pwd_bytes.decode("utf-8", errors="ignore"))


def verify_password(plain_password, hashed_password):
    # Apply the same logic for verification
    pwd_bytes = str(plain_password).encode("utf-8")
    if len(pwd_bytes) > 72:
        pwd_bytes = pwd_bytes[:72]
        
    return pwd_context.verify(pwd_bytes.decode("utf-8", errors="ignore"), hashed_password)


def get_market_data(lat, lng):
    try:
        # 1. Get Census Tract from FCC (This is a great, free way to do it)
        geo_url = f"https://geo.fcc.gov/api/census/block/find?latitude={lat}&longitude={lng}&format=json"
        geo_response = requests.get(geo_url)
        geo_response.raise_for_status()
        geo = geo_response.json()

        state = geo["State"]["FIPS"]
        county = geo["County"]["FIPS"]
        # The block FIPS is 15 digits: [SS][CCC][TTTTTT][BBBB]
        # Tract is digits 5 through 11
        tract = geo["Block"]["FIPS"][5:11]

        # 2. Query Census ACS 5-Year Data
        params = {
            "get": "B01003_001E,B19013_001E,B01002_001E", # Pop, Income, Age
            "for": f"tract:{tract}",
            "in": f"state:{state} county:{county}"
        }
        
        census_res = requests.get(CENSUS_API, params=params)
        
        # If Census has no data for this tract, return N/A instead of crashing
        if census_res.status_code != 200:
            return {"population": "N/A", "median_income": "N/A", "median_age": "N/A"}

        data = census_res.json()
        # Census returns [ ["header"], ["values"] ], so we want index 1
        stats = data[1]

        return {
            "population": stats[0],
            "median_income": stats[1],
            "median_age": stats[2]
        }
    except Exception as e:
        print(f"Census Error: {e}")
        return {"population": "Error", "median_income": "Error", "median_age": "Error"}


    
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

    # NEW: Get census market data
    market = get_market_data(client["lat"], client["lng"])

    return {
        "client": {
            "name": client["name"],
            "rating": client["rating"],
            "reviews": client["reviews"]
        },

        # NEW MARKET DATA
        "market_data": market,

        "keyword_detected": keyword,

        "radius_1_mile": get_nearby(
            client["lat"],
            client["lng"],
            miles_to_meters(1),
            keyword,
            client["place_id"]
        ),

        "radius_3_mile": get_nearby(
            client["lat"],
            client["lng"],
            miles_to_meters(3),
            keyword,
            client["place_id"]
        ),

        "radius_5_mile": get_nearby(
            client["lat"],
            client["lng"],
            miles_to_meters(5),
            keyword,
            client["place_id"]
        )
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
        # 1. Normalize email (just like signup)
        email_clean = data.email.lower().strip()
        user = db.query(User).filter(User.email == email_clean).first()

        if not user:
            return {"error": "Invalid credentials"}

        # 2. Verify password with the same truncation logic
        if not verify_password(data.password, user.password_hash):
            return {"error": "Invalid credentials"}

        # 3. Create token - check if SECRET_KEY exists
        if not SECRET_KEY:
             raise HTTPException(status_code=500, detail="Server Configuration Error: Missing SECRET_KEY")

        access_token = create_access_token(
            data={"user_id": user.id}
        )

        return {
            "access_token": access_token,
            "token_type": "bearer"
        }
    except Exception as e:
        # This will show you the EXACT error in the response body
        raise HTTPException(status_code=500, detail=f"Login error: {str(e)}")
    finally:
        db.close()

from fastapi import FastAPI, HTTPException

from pydantic import BaseModel

from database import engine, SessionLocal

from models import Base, User, Location, AnalysisResult

from sqlalchemy.orm import Session

from datetime import datetime, timedelta

from jose import jwt

from passlib.context import CryptContext

import requests

import os

import time

from groq import Groq
import json

from ddgs import DDGS
import json



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

#-------------------------------------------------

def ai_competitor_agent(business_name, competitors):
    """
    AI agent using DDGS for search and Groq (Llama 3.3) for analysis.
    """
    search_results = {}

    # 1. Perform real web searches
    with DDGS() as ddgs:
        for comp in competitors:
            try:
                # Crafting a query for 2026 pricing and data
                query = f"{comp} {business_name} pricing menu reviews promotions 2026"
                results = ddgs.text(query, max_results=5)
                search_results[comp] = list(results)
            except Exception as e:
                search_results[comp] = [{"error": str(e)}]

    # 2. Build prompt for Groq
    prompt = f"""
    You are a competitive intelligence analyst.
    Business: {business_name}
    Competitor Data: {json.dumps(search_results, indent=2)}

    Extract: pricing, menu items, promotions, strengths, weaknesses, sentiment, and USPs.
    Return ONLY a clean JSON object. Do not include introductory text.
    """

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile", 
        messages=[
            {"role": "system", "content": "You are a competitive intelligence analyst. Output JSON only."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"} 
    )

    # 3. Clean and return the content
    content = response.choices[0].message.content.strip()
    
    # Remove markdown formatting if the AI added it accidentally
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    return content
    
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

        return {"error": "Business not found"}


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

        "types": result.get("types", []),

        "rating": result.get("rating"),

        "reviews": result.get("user_ratings_total")

    }


# ---------------- MARKET DATA ----------------


def get_market_data(lat, lng):
    try:
        # 1. Get Geography IDs from Coordinates
        geo_url = f"https://geocoding.geo.census.gov/geocoder/geographies/coordinates?x={lng}&y={lat}&benchmark=Public_AR_Current&vintage=Current_Current&format=json"
        geo_res = requests.get(geo_url, timeout=10)
        geo_data = geo_res.json()

        # Extract specific IDs needed for the data API
        tract_info = geo_data["result"]["geographies"]["Census Tracts"][0]
        state = tract_info["STATE"]
        county = tract_info["COUNTY"]
        tract = tract_info["TRACT"]

        # 2. Get Actual Data (B01003=Pop, B19013=Income, B01002=Age)
        data_url = f"https://api.census.gov/data/2022/acs/acs5?get=B01003_001E,B19013_001E,B01002_001E&for=tract:{tract}&in=state:{state}%20county:{county}"
        data_res = requests.get(data_url, timeout=10)
        
        # Census returns a list of lists: [["header"], ["values"]]
        stats = data_res.json()[1] 

        return {
            "population": int(stats[0]) if stats[0] else 0,
            "median_income": int(stats[1]) if stats[1] else 0,
            "median_age": float(stats[2]) if stats[2] else 0.0
        }
    except Exception as e:
        print(f"Census Error: {e}")
        return {"population": 0, "median_income": 0, "median_age": 0.0}


# ---------------- COMPETITOR TYPE LOGIC ----------------


SUPPORTED_TYPES = {

    "cafe",

    "restaurant",

    "bakery",

    "bar",

    "gym",

    "dentist",

    "doctor",

    "beauty_salon",

    "hair_care",

    "car_repair",

    "lawyer",

    "real_estate_agency",

    "meal_takeaway",

    "meal_delivery"

}



def detect_business_type(types):


    for t in types:

        if t in SUPPORTED_TYPES:

            return t


    return None



# ---------------- GOOGLE NEARBY SEARCH ----------------


def get_nearby(lat, lng, radius, place_type, client_place_id):


    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


    params = {

        "location": f"{lat},{lng}",

        "radius": radius,

        "type": place_type,

        "key": GOOGLE_API_KEY

    }


    response = requests.get(url, params=params).json()


    results = response.get("results", [])


    while "next_page_token" in response:


        time.sleep(2)


        params = {

            "pagetoken": response["next_page_token"],

            "key": GOOGLE_API_KEY

        }


        response = requests.get(url, params=params).json()


        results.extend(response.get("results", []))


    competitors = []


    for place in results:


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

def competitors(business_name: str, address: str):


    client = get_client_info(business_name, address)


    if not client:

        return {"error": "Business not found"}


    business_type = detect_business_type(client["types"])


    market = get_market_data(client["lat"], client["lng"])


    radius1 = get_nearby(client["lat"], client["lng"], 1609, business_type, client["place_id"])

    radius3 = get_nearby(client["lat"], client["lng"], 4828, business_type, client["place_id"])

    radius5 = get_nearby(client["lat"], client["lng"], 8046, business_type, client["place_id"])


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


    summary = {

        "competitors_1_mile": len(radius1),

        "competitors_3_mile": len(radius3),

        "competitors_5_mile": len(radius5),

        "client_rating": client["rating"],

        "client_reviews": client["reviews"]

    }


    return {

        "client": {

            "name": client["name"],

            "rating": client["rating"],

            "reviews": client["reviews"]

        },

        "market_data": market,

        "summary": summary,

        "business_type_detected": business_type,

        "radius_1_mile": radius1,

        "radius_3_mile": radius3,

        "radius_5_mile": radius5

    }

# ---------------------------------------------------

@app.get("/ai-competitor-intel")
def ai_competitor_intel(business_name: str, address: str):
    # 1. Get the target business coordinates
    client_info = get_client_info(business_name, address)
    if not client_info:
        return {"error": "Business not found"}

    business_type = detect_business_type(client_info["types"])
    
    # 2. Perform 3 separate searches for the specific mile radiuses
    # 1609m = 1 mile | 4828m = 3 miles | 8046m = 5 miles
    radius1 = get_nearby(client_info["lat"], client_info["lng"], 1609, business_type, client_info["place_id"])
    radius3 = get_nearby(client_info["lat"], client_info["lng"], 4828, business_type, client_info["place_id"])
    radius5 = get_nearby(client_info["lat"], client_info["lng"], 8046, business_type, client_info["place_id"])

    # 3. Get the market data (Population/Income) for this location
    market = get_market_data(client_info["lat"], client_info["lng"])

    # 4. Use the 3-mile list for the AI deep-dive (best for local context)
    comp_names = [c["name"] for c in radius3][:5]
    report_raw = ai_competitor_agent(client_info["name"], comp_names)
    
    try:
        report_json = json.loads(report_raw)
    except:
        report_json = {"error": "AI response error", "raw": report_raw}
        
    # 5. Save the complete report to your History
    db: Session = SessionLocal()
    try:
        analysis = AnalysisResult(
            user_id=1,
            place_id=client_info["place_id"],
            business_name=client_info["name"],
            competitors_1_mile=len(radius1),
            competitors_3_mile=len(radius3),
            competitors_5_mile=len(radius5),
            population=market.get("population", 0),
            median_income=market.get("median_income", 0),
            median_age=market.get("median_age", 0),
            ai_competitor_report=json.dumps(report_json)
        )
        db.add(analysis)
        db.commit()
    finally:
        db.close()

    return {
        "client": client_info["name"],
        "counts": {
            "1_mile": len(radius1),
            "3_mile": len(radius3),
            "5_mile": len(radius5)
        },
        "market": market,
        "ai_report": report_json
    }# ---------------- ANALYSIS HISTORY ----------------


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



# ---------------- SIGNUP ----------------


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


        return {

            "status": "account_created",

            "business": client["name"],

            "place_id": client["place_id"]

        }


    finally:

        db.close()



# ---------------- LOGIN ----------------


@app.post("/login")

def login(data: LoginRequest):


    db: Session = SessionLocal()


    try:


        email_clean = data.email.lower().strip()


        user = db.query(User).filter(User.email == email_clean).first()


        if not user or not verify_password(data.password, user.password_hash):

            return {"error": "Invalid credentials"}


        access_token = create_access_token(data={"user_id": user.id})


        return {

            "access_token": access_token,

            "token_type": "bearer"

        }


    finally:

        db.close()






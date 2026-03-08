from database import engine
from models import Base
from fastapi import FastAPI
import requests
import os
import time
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User, Location
from datetime import datetime


app = FastAPI()

Base.metadata.create_all(bind=engine)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}

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
        return {
            "error": "Business not found. Please use the exact business name from Google Maps."
        }

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

def miles_to_meters(miles):
    return int(miles * 1609.34)


def infer_keyword(name, types):
    name = name.lower()

    if "pizza" in name:
        return "pizza"

    if "dentist" in types:
        return "dentist"

    if "plumber" in types:
        return "plumber"

    if "beauty_salon" in types:
        return "salon"

    if "gym" in types:
        return "gym"

    return None


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

        competitors.append({
            "name": place.get("name"),
            "rating": place.get("rating"),
            "reviews": place.get("user_ratings_total"),
            "address": place.get("vicinity"),
            "place_id": place.get("place_id")
        })

    return competitors


@app.get("/competitors")
def competitors(business_name: str, address: str):

    client = get_client_info(business_name, address)

    if not client:
        return {"error": "Business not found"}

    keyword = infer_keyword(client["name"], client["types"])

    radius1 = get_nearby(
        client["lat"],
        client["lng"],
        miles_to_meters(1),
        keyword,
        client["place_id"]
    )

    radius3 = get_nearby(
        client["lat"],
        client["lng"],
        miles_to_meters(3),
        keyword,
        client["place_id"]
    )

    radius5 = get_nearby(
        client["lat"],
        client["lng"],
        miles_to_meters(5),
        keyword,
        client["place_id"]
    )

    return {
        "client": {
            "name": client["name"],
            "rating": client["rating"],
            "reviews": client["reviews"]
        },
        "keyword_detected": keyword,
        "radius_1_mile": radius1,
        "radius_3_mile": radius3,
        "radius_5_mile": radius5
    }
@app.post("/signup")
def signup(email: str, password: str, business_name: str, address: str):

    db: Session = SessionLocal()

    # Check if user already exists
    existing = db.query(User).filter(User.email == email).first()

    if existing:
        return {"error": "User already exists"}

    # Find the business on Google
    client = get_client_info(business_name, address)

    if not client:
        return {"error": "Business not found. Please use your exact Google Maps business name."}

    # Create user
    new_user = User(
        email=email,
        password_hash=password,
        created_at=datetime.utcnow()
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Lock business location
    location = Location(
        user_id=new_user.id,
        business_name=client["name"],
        address=address,
        place_id=client["place_id"]
    )

    db.add(location)
    db.commit()

    return {
        "status": "account_created",
        "business": client["name"],
        "place_id": client["place_id"]
    }

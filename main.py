from fastapi import FastAPI
import requests
import os

app = FastAPI()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}

@app.get("/competitors")
def get_competitors(address: str, category: str, radius: int = 3000):
    
    # Step 1: Geocode address
    geo_url = f"https://maps.googleapis.com/maps/api/geocode/json"
    geo_params = {
        "address": address,
        "key": GOOGLE_API_KEY
    }
    geo_response = requests.get(geo_url, params=geo_params).json()

    if not geo_response["results"]:
        return {"error": "Invalid address"}

    location = geo_response["results"][0]["geometry"]["location"]
    lat = location["lat"]
    lng = location["lng"]

    # Step 2: Search nearby places
    places_url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    places_params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "keyword": category,
        "key": GOOGLE_API_KEY
    }

    places_response = requests.get(places_url, params=places_params).json()

    competitors = []

    for place in places_response.get("results", []):
        competitors.append({
            "name": place.get("name"),
            "rating": place.get("rating"),
            "reviews": place.get("user_ratings_total"),
            "address": place.get("vicinity")
        })

    return {
        "address": address,
        "category": category,
        "competitors": competitors
    }

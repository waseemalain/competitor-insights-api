from fastapi import FastAPI
import requests
import os

app = FastAPI()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}

@app.get("/competitors")
def get_competitors(address: str, category: str, limit: int = 20):
    if not GOOGLE_API_KEY:
        return {"error": "Missing GOOGLE_API_KEY env var"}

    # Geocode address (so we can echo a center point back; optional but useful)
    geo_url = "https://maps.googleapis.com/maps/api/geocode/json"
    geo_params = {"address": address, "key": GOOGLE_API_KEY}
    geo_response = requests.get(geo_url, params=geo_params).json()

    if geo_response.get("status") != "OK" or not geo_response.get("results"):
        return {
            "error": "Geocoding failed",
            "geocode_status": geo_response.get("status"),
            "geocode_response": geo_response,
        }

    loc = geo_response["results"][0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]

    # Places Text Search (reliable): "<category> near <address>"
    text_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query = f"{category} near {address}"
    text_params = {"query": query, "key": GOOGLE_API_KEY}
    text_response = requests.get(text_url, params=text_params).json()

    status = text_response.get("status")
    if status not in ["OK", "ZERO_RESULTS"]:
        return {
            "error": "Places text search failed",
            "places_status": status,
            "places_response": text_response,
        }

    results = text_response.get("results", [])[: max(1, min(limit, 60))]

    competitors = [{
        "name": p.get("name"),
        "rating": p.get("rating"),
        "reviews": p.get("user_ratings_total"),
        "address": p.get("formatted_address"),
        "place_id": p.get("place_id"),
    } for p in results]

    return {
        "query": query,
        "center": {"lat": lat, "lng": lng},
        "competitors_found": len(competitors),
        "competitors": competitors,
        "places_status": status,
    }

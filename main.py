from fastapi import FastAPI
import requests
import os
import time

app = FastAPI()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}


def get_client_place_id(business_name: str, address: str):
    search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query = f"{business_name} {address}"
    params = {"query": query, "key": GOOGLE_API_KEY}
    response = requests.get(search_url, params=params).json()

    if response.get("results"):
        return response["results"][0].get("place_id")

    return None


@app.get("/competitors")
def get_competitors(business_name: str, address: str, category: str):

    if not GOOGLE_API_KEY:
        return {"error": "Missing GOOGLE_API_KEY"}

    # 1️⃣ Get client place_id
    client_place_id = get_client_place_id(business_name, address)

    # 2️⃣ Search competitors
    search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query = f"{category} near {address}"
    params = {"query": query, "key": GOOGLE_API_KEY}

    response = requests.get(search_url, params=params).json()

    if response.get("status") not in ["OK", "ZERO_RESULTS"]:
        return {"error": "Places search failed", "details": response}

    all_results = response.get("results", [])

    # Handle pagination (up to 3 pages)
    while "next_page_token" in response:
        time.sleep(2)  # required by Google
        params = {
            "pagetoken": response["next_page_token"],
            "key": GOOGLE_API_KEY,
        }
        response = requests.get(search_url, params=params).json()
        all_results.extend(response.get("results", []))

    competitors = []

    for place in all_results:
        # 🚫 Exclude client itself
        if place.get("place_id") == client_place_id:
            continue

        competitors.append({
            "name": place.get("name"),
            "rating": place.get("rating"),
            "reviews": place.get("user_ratings_total"),
            "address": place.get("formatted_address"),
            "place_id": place.get("place_id"),
        })

    return {
        "client_place_id": client_place_id,
        "competitors_found": len(competitors),
        "competitors": competitors
    }

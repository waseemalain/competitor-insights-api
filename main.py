from fastapi import FastAPI
import requests
import os
import time
import math

app = FastAPI()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


@app.get("/")
def root():
    return {"status": "Pali Analytics API running"}


def miles_to_meters(miles):
    return int(miles * 1609.34)


def get_client_info(business_name, address):
    search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query = f"{business_name} {address}"
    params = {"query": query, "key": GOOGLE_API_KEY}

    response = requests.get(search_url, params=params).json()

    if not response.get("results"):
        return None, None, None

    result = response["results"][0]

    place_id = result.get("place_id")
    lat = result["geometry"]["location"]["lat"]
    lng = result["geometry"]["location"]["lng"]

    return place_id, lat, lng


def get_nearby_competitors(lat, lng, radius_meters, keyword, client_place_id):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius_meters,
        "type": "restaurant",
        "keyword": keyword,
        "key": GOOGLE_API_KEY,
    }

    response = requests.get(url, params=params).json()

    results = response.get("results", [])

    # Pagination (max 3 pages)
    while "next_page_token" in response:
        time.sleep(2)
        params = {
            "pagetoken": response["next_page_token"],
            "key": GOOGLE_API_KEY,
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
            "place_id": place.get("place_id"),
        })

    return competitors


@app.get("/competitors")
def competitors(business_name: str, address: str, category: str):

    if not GOOGLE_API_KEY:
        return {"error": "Missing GOOGLE_API_KEY"}

    client_place_id, lat, lng = get_client_info(business_name, address)

    if not lat:
        return {"error": "Client business not found"}

    radius_1 = get_nearby_competitors(
        lat, lng, miles_to_meters(1), category, client_place_id
    )

    radius_3 = get_nearby_competitors(
        lat, lng, miles_to_meters(3), category, client_place_id
    )

    radius_5 = get_nearby_competitors(
        lat, lng, miles_to_meters(5), category, client_place_id
    )

    return {
        "client_place_id": client_place_id,
        "radius_1_mile": radius_1,
        "radius_3_mile": radius_3,
        "radius_5_mile": radius_5,
    }

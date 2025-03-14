from flask import Flask, request, jsonify
import googlemaps

app = Flask(__name__)

# Replace this with your actual Google Maps API Key
API_KEY = "AIzaSyCGVV0DNXMdOWRDfQ86Y51ikxhiSlbIlxA"
gmaps = googlemaps.Client(key=API_KEY)

@app.route('/get_insights', methods=['POST'])
def get_insights():
    data = request.json
    address = data.get("address")
    category = data.get("category")

    # Get latitude & longitude from address
    geocode_result = gmaps.geocode(address)
    if not geocode_result:
        return jsonify({"error": "Invalid address"}), 400

    location = geocode_result[0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    # Get competitor data within 3 miles
    places_result = gmaps.places_nearby(
        location=(lat, lng),
        radius=4828,  # 3 miles in meters
        keyword=category
    )

    competitors = []
    for place in places_result.get("results", []):
        competitors.append({
            "name": place.get("name", "N/A"),
            "address": place.get("vicinity", "N/A"),
            "rating": place.get("rating", "N/A"),
            "reviews": place.get("user_ratings_total", "N/A")
        })

    return jsonify(competitors)

if __name__ == '__main__':
    app.run(debug=True)

from flask import Flask, request, jsonify
import googlemaps

app = Flask(__name__)

API_KEY = "AIzaSyCGVV0DNXMdOWRDfQ86Y51ikxhiSlbIlxA"
gmaps = googlemaps.Client(key=API_KEY)

@app.route('/get_insights', methods=['POST'])
def get_insights():
    data = request.json
    address = data.get("address")
    category = data.get("category")

    geocode_result = gmaps.geocode(address)
    if not geocode_result:
        return jsonify({"error": "Invalid address"}), 400

    location = geocode_result[0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    places_result = gmaps.places_nearby(
        location=(lat, lng),
        radius=4828,
        keyword=category
    )

    competitors = [{"name": place["name"], "address": place.get("vicinity", "N/A"),
                    "rating": place.get("rating", "N/A"), "reviews": place.get("user_ratings_total", "N/A")}
                   for place in places_result.get("results", [])]

    return jsonify(competitors)

if __name__ == '__main__':
    app.run(debug=True)


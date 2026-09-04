"""
tool/weather.py
=================
Real current-weather lookup via Open-Meteo (open-meteo.com) -- completely
free, no API key, no signup. Two calls: geocode the place name to
lat/lon, then fetch current conditions for those coordinates.
"""

import requests


def get_weather(location: str) -> tuple[bool, str]:
    """Get the current real weather/temperature for a place. Input: a city/place name, e.g. 'Thane, India'."""
    location = location.strip()
    if not location:
        return False, "I need a place name to check the weather for."

    try:
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
            timeout=10,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        results = geo_data.get("results")
        if not results:
            return False, f"Couldn't find a location matching '{location}'."

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        display_name = f"{place.get('name', location)}, {place.get('country', '')}".strip(", ")

        weather_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code",
                "timezone": "auto",
            },
            timeout=10,
        )
        weather_resp.raise_for_status()
        current = weather_resp.json().get("current", {})
        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")

        if temp is None:
            return False, f"Got a location for '{location}' but no current weather data back."

        return True, f"{display_name}: {temp}°C, {humidity}% humidity (live reading, just now)."
    except Exception as e:
        return False, f"Error checking weather: {e}"
from sqlalchemy import func


def haversine_km(lat_col, lng_col, lat: float, lng: float):
    """SQL expression for the distance in km between a lat/lng column pair and a point."""
    return 6371 * 2 * func.asin(func.sqrt(
        func.power(func.sin(func.radians(lat_col - lat) / 2), 2)
        + func.cos(func.radians(lat)) * func.cos(func.radians(lat_col))
        * func.power(func.sin(func.radians(lng_col - lng) / 2), 2)
    ))
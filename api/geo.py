"""Distance filtering without PostGIS.

ADR-011. At national scale this table holds roughly 950 stores, so a haversine
expression over all of them is a sub-millisecond sequential scan -- cheaper
than maintaining a GIST index and one less extension to install on every
environment. A bounding box narrows the candidates first so the trigonometry
runs on a handful of rows.

Straight-line distance is used for *filtering only*. Ranking and the travel
penalty use driving time, because in a dense metro the two diverge enough to
change the answer (docs/04-ALGORITHMS.md §5).

Revisit if the store count grows by an order of magnitude or if per-item geo
queries appear; at that point PostGIS earns its dependency.
"""

from __future__ import annotations

import math

from sqlalchemy import Float, and_, func
from sqlalchemy.sql.elements import ColumnElement

EARTH_RADIUS_KM = 6371.0
KM_PER_DEGREE_LAT = 111.32

# Israel spans about 29.5N to 33.3N; cos() barely moves across it, but the
# bounding box is computed per query anyway so this stays correct anywhere.
MIN_COS = 0.1

# Urban driving averages well under the posted limit once junctions and parking
# are counted. Used only until Distance Matrix results are cached.
ASSUMED_URBAN_KMH = 25.0


def haversine_km(lat_column, lng_column, lat: float, lng: float) -> ColumnElement[float]:
    """Great-circle distance in kilometres, as a SQL expression."""
    lat_rad = math.radians(lat)
    return (
        2
        * EARTH_RADIUS_KM
        * func.asin(
            func.sqrt(
                func.power(func.sin(func.radians(lat_column - lat) / 2), 2)
                + math.cos(lat_rad)
                * func.cos(func.radians(lat_column))
                * func.power(func.sin(func.radians(lng_column - lng) / 2), 2)
            )
        )
    ).cast(Float)


def bounding_box(lat_column, lng_column, lat: float, lng: float, radius_km: float):
    """Cheap pre-filter so haversine only runs on plausible rows."""
    lat_delta = radius_km / KM_PER_DEGREE_LAT
    cos_lat = max(abs(math.cos(math.radians(lat))), MIN_COS)
    lng_delta = radius_km / (KM_PER_DEGREE_LAT * cos_lat)

    return and_(
        lat_column.isnot(None),
        lng_column.isnot(None),
        lat_column.between(lat - lat_delta, lat + lat_delta),
        lng_column.between(lng - lng_delta, lng + lng_delta),
    )


def estimate_travel_minutes(distance_km: float) -> float:
    """Stand-in until Distance Matrix results are cached.

    Deliberately pessimistic: overstating travel time makes the system
    under-recommend splitting, which is the safer direction to be wrong in.
    """
    if distance_km <= 0:
        return 0.0
    return round(distance_km / ASSUMED_URBAN_KMH * 60, 1)

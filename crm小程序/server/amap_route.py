"""Driving-route adapter for the standalone trip reimbursement test module."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


class RouteAdapterError(Exception):
    pass


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise RouteAdapterError(f"{field}格式不正确") from error
    if not math.isfinite(result):
        raise RouteAdapterError(f"{field}格式不正确")
    return result


def normalize_point(value: dict[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RouteAdapterError(f"{field}不能为空")
    longitude = _number(value.get("longitude"), f"{field}经度")
    latitude = _number(value.get("latitude"), f"{field}纬度")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise RouteAdapterError(f"{field}经纬度超出有效范围")
    return {
        "label": str(value.get("label") or f"{longitude:.6f},{latitude:.6f}"),
        "longitude": longitude,
        "latitude": latitude,
    }


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _haversine_meters(origin: dict[str, Any], destination: dict[str, Any]) -> float:
    radius = 6_371_000
    lat1 = math.radians(origin["latitude"])
    lat2 = math.radians(destination["latitude"])
    delta_lat = lat2 - lat1
    delta_lon = math.radians(destination["longitude"] - origin["longitude"])
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


class MockRouteAdapter:
    mode = "MOCK_ESTIMATE"

    def geocode_address(self, address: str, city: str = "") -> dict[str, Any]:
        raise RouteAdapterError("未配置高德Key，暂不能按地址解析坐标")

    def reverse_geocode(self, point: dict[str, Any]) -> dict[str, Any]:
        raise RouteAdapterError("未配置高德Key，暂不能根据位置获取地址")

    def calculate_driving(self, origin: dict[str, Any], destination: dict[str, Any]) -> dict[str, Any]:
        start = normalize_point(origin, "出发位置")
        end = normalize_point(destination, "到达位置")
        distance_meters = max(100, round(_haversine_meters(start, end) * 1.25))
        duration_seconds = round(distance_meters / 13.9)
        return {
            "source": self.mode,
            "distanceMeters": distance_meters,
            "distanceKm": round(distance_meters / 1000, 1),
            "durationSeconds": duration_seconds,
            "durationMinutes": max(1, round(duration_seconds / 60)),
            "estimatedTollAmount": 0,
            "tollDistanceMeters": 0,
            "tollRoads": [],
            "strategy": "直线距离道路系数模拟",
            "calculatedAt": _timestamp(),
            "alternatives": [],
            "selectionMode": "MOCK_ONLY",
        }


class AmapRouteAdapter:
    mode = "AMAP_LIVE"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 15,
        fetch_json: Callable[[str, int], dict[str, Any]] | None = None,
    ):
        if not api_key:
            raise RouteAdapterError("未配置高德Web服务Key")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.fetch_json = fetch_json or self._fetch_json

    @staticmethod
    def _fetch_json(url: str, timeout_seconds: int) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": "crm-trip-test/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RouteAdapterError("高德路线服务暂时不可用，请稍后重试") from error

    def calculate_driving(self, origin: dict[str, Any], destination: dict[str, Any]) -> dict[str, Any]:
        start = normalize_point(origin, "出发位置")
        end = normalize_point(destination, "到达位置")
        params = urllib.parse.urlencode(
            {
                "key": self.api_key,
                "origin": f"{start['longitude']:.6f},{start['latitude']:.6f}",
                "destination": f"{end['longitude']:.6f},{end['latitude']:.6f}",
                "extensions": "all",
                "strategy": "10",
                "output": "json",
            }
        )
        payload = self.fetch_json(
            f"https://restapi.amap.com/v3/direction/driving?{params}",
            self.timeout_seconds,
        )
        paths = (payload.get("route") or {}).get("paths") or []
        if str(payload.get("status")) != "1" or not paths:
            message = payload.get("info") or "没有找到可用驾车路线"
            raise RouteAdapterError(f"高德路线计算失败：{message}")
        calculated_at = _timestamp()
        alternatives = []
        for index, path in enumerate(paths):
            steps = path.get("steps") or []
            toll_roads = list(
                dict.fromkeys(
                    str(step.get("toll_road") or "").strip()
                    for step in steps
                    if str(step.get("toll_road") or "").strip()
                )
            )
            distance_meters = int(float(path.get("distance") or 0))
            duration_seconds = int(float(path.get("duration") or 0))
            alternatives.append(
                {
                    "candidateId": f"route-{index + 1}",
                    "source": self.mode,
                    "distanceMeters": distance_meters,
                    "distanceKm": round(distance_meters / 1000, 1),
                    "durationSeconds": duration_seconds,
                    "durationMinutes": max(1, round(duration_seconds / 60)),
                    "estimatedTollAmount": round(float(path.get("tolls") or 0), 2),
                    "tollDistanceMeters": int(float(path.get("toll_distance") or 0)),
                    "tollRoads": toll_roads,
                    "strategy": str(path.get("strategy") or "高德路线"),
                    "calculatedAt": calculated_at,
                }
            )

        minimum_duration = min(item["durationMinutes"] for item in alternatives)
        minimum_toll = min(item["estimatedTollAmount"] for item in alternatives)
        for item in alternatives:
            if item["durationMinutes"] == minimum_duration:
                item["routeLabel"] = "时间较短"
            elif item["estimatedTollAmount"] == minimum_toll:
                item["routeLabel"] = "费用较低"
            else:
                item["routeLabel"] = "备选路线"

        selected = min(
            alternatives,
            key=lambda item: item["durationMinutes"]
            + item["estimatedTollAmount"] * 0.6,
        )
        return {
            **selected,
            "alternatives": alternatives,
            "selectionMode": "TIME_TOLL_BALANCED",
        }

    def geocode_address(self, address: str, city: str = "") -> dict[str, Any]:
        normalized_address = str(address or "").strip()
        if not normalized_address:
            raise RouteAdapterError("地址不能为空")
        params = urllib.parse.urlencode(
            {
                "key": self.api_key,
                "address": normalized_address,
                "city": str(city or "").strip(),
                "output": "json",
            }
        )
        payload = self.fetch_json(
            f"https://restapi.amap.com/v3/geocode/geo?{params}",
            self.timeout_seconds,
        )
        geocodes = payload.get("geocodes") or []
        if str(payload.get("status")) != "1" or not geocodes:
            message = payload.get("info") or "没有找到该地址"
            raise RouteAdapterError(f"高德地址解析失败：{message}")
        match = geocodes[0]
        try:
            longitude, latitude = str(match.get("location") or "").split(",", 1)
        except ValueError as error:
            raise RouteAdapterError("高德未返回有效地址坐标") from error
        return {
            "source": self.mode,
            "query": normalized_address,
            "formattedAddress": str(match.get("formatted_address") or normalized_address),
            "longitude": _number(longitude, "经度"),
            "latitude": _number(latitude, "纬度"),
            "province": str(match.get("province") or ""),
            "city": str(match.get("city") or ""),
            "district": str(match.get("district") or ""),
            "level": str(match.get("level") or ""),
        }

    def reverse_geocode(self, point: dict[str, Any]) -> dict[str, Any]:
        location = normalize_point(point, "当前位置")
        params = urllib.parse.urlencode(
            {
                "key": self.api_key,
                "location": f"{location['longitude']:.6f},{location['latitude']:.6f}",
                "radius": "300",
                "extensions": "all",
                "homeorcorp": "2",
                "output": "json",
            }
        )
        payload = self.fetch_json(
            f"https://restapi.amap.com/v3/geocode/regeo?{params}",
            self.timeout_seconds,
        )
        regeocode = payload.get("regeocode") or {}
        formatted_address = str(regeocode.get("formatted_address") or "").strip()
        if str(payload.get("status")) != "1" or not formatted_address:
            message = payload.get("info") or "没有找到当前位置地址"
            raise RouteAdapterError(f"高德位置解析失败：{message}")
        pois = regeocode.get("pois") or []
        nearest_poi = pois[0] if pois else {}
        place_name = str(nearest_poi.get("name") or "").strip()
        component = regeocode.get("addressComponent") or {}
        return {
            "source": self.mode,
            "placeName": place_name or formatted_address,
            "formattedAddress": formatted_address,
            "longitude": location["longitude"],
            "latitude": location["latitude"],
            "province": str(component.get("province") or ""),
            "city": str(component.get("city") or ""),
            "district": str(component.get("district") or ""),
            "nearestPoiDistance": str(nearest_poi.get("distance") or ""),
        }


def create_route_adapter_from_environment() -> MockRouteAdapter | AmapRouteAdapter:
    api_key = os.environ.get("AMAP_WEB_SERVICE_KEY", "").strip()
    if api_key:
        return AmapRouteAdapter(
            api_key,
            timeout_seconds=int(os.environ.get("AMAP_TIMEOUT_SECONDS", "15")),
        )
    return MockRouteAdapter()

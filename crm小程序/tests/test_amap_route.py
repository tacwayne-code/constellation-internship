import unittest
from urllib.parse import parse_qs, urlparse

from server.amap_route import AmapRouteAdapter, MockRouteAdapter, RouteAdapterError


class AmapRouteAdapterTest(unittest.TestCase):
    def test_live_adapter_parses_distance_duration_and_tolls(self):
        captured = {}

        def fake_fetch(url, timeout_seconds):
            captured["url"] = url
            captured["timeout"] = timeout_seconds
            return {
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "123456",
                            "duration": "7200",
                            "tolls": "68.5",
                            "toll_distance": "101000",
                            "strategy": "高德推荐",
                            "steps": [
                                {"toll_road": "广深沿江高速"},
                                {"toll_road": "广深沿江高速"},
                                {"toll_road": "京港澳高速"},
                            ],
                        }
                    ]
                },
            }

        adapter = AmapRouteAdapter("server-only-key", fetch_json=fake_fetch)
        result = adapter.calculate_driving(
            {"label": "广州", "longitude": 113.32446, "latitude": 23.10647},
            {"label": "深圳", "longitude": 113.93041, "latitude": 22.53332},
        )

        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["origin"], ["113.324460,23.106470"])
        self.assertEqual(query["destination"], ["113.930410,22.533320"])
        self.assertEqual(query["key"], ["server-only-key"])
        self.assertEqual(query["extensions"], ["all"])
        self.assertEqual(result["source"], "AMAP_LIVE")
        self.assertEqual(result["distanceKm"], 123.5)
        self.assertEqual(result["durationMinutes"], 120)
        self.assertEqual(result["estimatedTollAmount"], 68.5)
        self.assertEqual(result["tollRoads"], ["广深沿江高速", "京港澳高速"])

    def test_mock_adapter_supports_full_flow_without_key(self):
        result = MockRouteAdapter().calculate_driving(
            {"label": "A", "longitude": 113.3, "latitude": 23.1},
            {"label": "B", "longitude": 113.9, "latitude": 22.5},
        )

        self.assertEqual(result["source"], "MOCK_ESTIMATE")
        self.assertGreater(result["distanceKm"], 0)
        self.assertEqual(result["estimatedTollAmount"], 0)

    def test_invalid_coordinate_is_rejected_before_upstream_call(self):
        with self.assertRaises(RouteAdapterError):
            MockRouteAdapter().calculate_driving(
                {"label": "A", "longitude": 300, "latitude": 23.1},
                {"label": "B", "longitude": 113.9, "latitude": 22.5},
            )

    def test_reverse_geocode_returns_nearest_place_and_formatted_address(self):
        def fake_fetch(url, timeout_seconds):
            self.assertIn("/v3/geocode/regeo?", url)
            self.assertIn("extensions=all", url)
            return {
                "status": "1",
                "regeocode": {
                    "formatted_address": "广东省中山市东区街道测试路1号",
                    "addressComponent": {
                        "province": "广东省",
                        "city": "中山市",
                        "district": "东区街道",
                    },
                    "pois": [{"name": "测试大厦", "distance": "18"}],
                },
            }

        adapter = AmapRouteAdapter("server-only-key", fetch_json=fake_fetch)
        result = adapter.reverse_geocode(
            {"label": "当前位置", "longitude": 113.39, "latitude": 22.52}
        )

        self.assertEqual(result["placeName"], "测试大厦")
        self.assertEqual(result["formattedAddress"], "广东省中山市东区街道测试路1号")
        self.assertEqual(result["longitude"], 113.39)

    def test_company_search_returns_addresses_without_phone_or_contact(self):
        captured = {}
        def fetch(url, timeout):
            captured["url"] = url
            return {"status": "1", "pois": [
                {"id": "P1", "name": "测试企业", "pname": "广东省", "cityname": "深圳市", "adname": "南山区",
                 "address": "科技路1号", "tel": "18811112222", "contact": "不应返回"},
                {"id": "P2", "name": "无详细地址", "address": []}]}
        result = AmapRouteAdapter("test-server-key", fetch_json=fetch).search_places("测试企业")
        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["keywords"], ["测试企业"])
        self.assertEqual(query["offset"], ["8"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["formattedAddress"], "广东省深圳市南山区科技路1号")
        self.assertNotIn("tel", result[0])
        self.assertNotIn("contact", result[0])
        self.assertNotIn("test-server-key", str(result))

    def test_company_search_empty_failure_and_missing_key(self):
        adapter = AmapRouteAdapter("key", fetch_json=lambda *_: {"status": "1", "pois": []})
        self.assertEqual(adapter.search_places("不存在"), [])
        with self.assertRaises(RouteAdapterError):
            adapter.search_places("")
        with self.assertRaises(RouteAdapterError):
            MockRouteAdapter().search_places("企业")
        with self.assertRaises(RouteAdapterError):
            AmapRouteAdapter("key", fetch_json=lambda *_: {"status": "0"}).search_places("企业")


if __name__ == "__main__":
    unittest.main()

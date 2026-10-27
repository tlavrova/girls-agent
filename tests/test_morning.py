import unittest

from girls_agent import morning, weather

RSS = b"""<?xml version="1.0"?><rss><channel><title>World</title>
<item><title>Big news</title><description>Something happened.</description></item>
<item><title>Second</title></item>
<item><title></title><description>no title, skipped</description></item>
</channel></rss>"""


class MorningTest(unittest.TestCase):
    def test_describe_weather(self):
        self.assertEqual(weather.describe(0), ("☀️", "ясно"))
        self.assertEqual(weather.describe(53), ("🌦", "морось"))
        self.assertEqual(weather.describe(95), ("⛈", "гроза"))

    def test_weather_summary(self):
        daily = {"weather_code": [61], "temperature_2m_min": [11.7], "temperature_2m_max": [15.6],
                 "precipitation_probability_max": [100], "wind_speed_10m_max": [27.4]}
        self.assertEqual(weather.summary(daily), (
            "🌧", "дождь, от 12° до 16°, вероятность осадков 100%, ветер до 27 км/ч"))

    def test_parse_headlines(self):
        self.assertEqual(morning.parse_headlines(RSS), ["Big news: Something happened.", "Second"])
        self.assertEqual(len(morning.parse_headlines(RSS, limit=1)), 1)


if __name__ == "__main__":
    unittest.main()

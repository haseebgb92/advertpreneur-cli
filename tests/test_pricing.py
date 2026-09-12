import unittest

from advertpreneur_cli.pricing import DEFAULT_FREE_CLOUD_MODELS, DEFAULT_PRICES, is_free_cloud_model, lookup_price


class PricingTests(unittest.TestCase):
    def test_variant_price_aliases(self):
        self.assertIsNotNone(lookup_price(DEFAULT_PRICES, "gemma4:31b"))
        self.assertIsNotNone(lookup_price(DEFAULT_PRICES, "nemotron-3-nano:30b"))
        self.assertIsNotNone(lookup_price(DEFAULT_PRICES, "deepseek-v4-flash:0731"))
        self.assertIsNotNone(lookup_price(DEFAULT_PRICES, "mistral-large-3:675b"))
        self.assertIsNotNone(lookup_price(DEFAULT_PRICES, "gpt-oss:120b"))

    def test_free_starter_pool(self):
        self.assertTrue(is_free_cloud_model("gpt-oss:20b", DEFAULT_FREE_CLOUD_MODELS))
        self.assertTrue(is_free_cloud_model("nemotron-3-ultra:cloud", DEFAULT_FREE_CLOUD_MODELS))
        self.assertFalse(is_free_cloud_model("glm-5.3-flash", DEFAULT_FREE_CLOUD_MODELS))


if __name__ == "__main__":
    unittest.main()

"""Server defaults. No network."""

import unittest

from liqmap.server import _parser


class ServerDefaultsTests(unittest.TestCase):
    def test_binds_localhost_by_default(self):
        args = _parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)


if __name__ == "__main__":
    unittest.main()

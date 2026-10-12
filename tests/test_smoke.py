# SPDX-License-Identifier: AGPL-3.0-only
import unittest

import larder


class SmokeTest(unittest.TestCase):
    def test_package_imports_and_has_version(self) -> None:
        self.assertIsInstance(larder.__version__, str)
        self.assertTrue(larder.__version__)

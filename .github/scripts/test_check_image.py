"""Protect the publication gate against mismatched real image metadata."""
import copy
import unittest

from check_image import validate_image


class ImageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.image = {'Os': 'linux', 'Architecture': 'arm64', 'Config': {
            'Labels': {'io.hass.type': 'app', 'io.hass.arch': 'aarch64',
                       'io.hass.version': '1.2.3'}, 'Cmd': ['/run.sh']}}

    def test_matching_arm64_image_is_accepted(self):
        validate_image(self.image, 'aarch64', '1.2.3')

    def test_wrong_or_missing_labels_are_rejected(self):
        for key in self.image['Config']['Labels']:
            for value in (None, 'wrong'):
                with self.subTest(key=key, value=value):
                    image = copy.deepcopy(self.image)
                    image['Config']['Labels'][key] = value
                    with self.assertRaises(ValueError):
                        validate_image(image, 'aarch64', '1.2.3')

    def test_matching_label_does_not_hide_wrong_architecture(self):
        self.image['Architecture'] = 'amd64'
        with self.assertRaises(ValueError):
            validate_image(self.image, 'aarch64', '1.2.3')

    def test_non_linux_image_is_rejected(self):
        self.image['Os'] = 'windows'
        with self.assertRaises(ValueError):
            validate_image(self.image, 'aarch64', '1.2.3')

    def test_image_without_command_is_rejected(self):
        self.image['Config']['Cmd'] = None
        with self.assertRaises(ValueError):
            validate_image(self.image, 'aarch64', '1.2.3')


if __name__ == '__main__':
    unittest.main()

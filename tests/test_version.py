from importlib.metadata import version

import cpre


def test_public_version_matches_package_metadata():
    assert cpre.__version__ == version("cpre")

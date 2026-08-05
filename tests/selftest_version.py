#!/usr/bin/env python3
from app.version import component_version, release_version

assert release_version() == "8.0.2", release_version()
assert component_version("camera-entropy-test").endswith(".8.0.2")
print("version helper self-test: PASS")

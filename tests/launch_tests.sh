#!/bin/sh
# exit when any command fails
set -e

# clean all existing cache and previous build artifacts
rm -rf /tmp/venv && virtualenv /tmp/venv && . /tmp/venv/bin/activate
rm -rf .pytest_cache
find . -name "*.pyc" -o -name "__pycache__" -exec rm -rfv {} \;

# setup for tests
pip install -r requirements_dev.txt -U
python setup.py build_version
python setup.py build_pbf

# launch tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. KIRIN_CONFIG_FILE=test_settings.py \
	py.test -v --doctest-modules -p no:cacheprovider \
	--cov-report xml --junitxml=pytest_kirin.xml --cov=kirin .

# Final clean
rm -rf /tmp/venv

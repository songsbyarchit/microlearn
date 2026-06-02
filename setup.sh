#!/bin/bash
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11
python3.11 -m pip install -r requirements.txt

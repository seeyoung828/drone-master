#!/bin/bash

python3 esp_node.py esp_01 9001 40 &
python3 esp_node.py esp_02 9002 45 &
python3 esp_node.py esp_03 9003 50 &
python3 esp_node.py esp_04 9004 35 &
python3 esp_node.py esp_05 9005 60 &
python3 esp_node.py esp_06 9006 38 &
python3 esp_node.py esp_07 9007 42 &
python3 esp_node.py esp_08 9008 55 &
python3 esp_node.py esp_09 9009 30 &
python3 esp_node.py esp_10 9010 48 &

wait
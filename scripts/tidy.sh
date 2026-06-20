#!/usr/bin/env bash

rm -r $(find | grep __pycache__$)
make -C runtime clean

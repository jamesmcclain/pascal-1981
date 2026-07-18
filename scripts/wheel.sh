#!/usr/bin/env bash

docker run --rm -v "$PWD":/work pascal-1981:latest sh -c "pip wheel . --no-deps -w /work/dist"

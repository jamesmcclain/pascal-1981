#!/usr/bin/bash

isort $(find | grep '\.py$')
yapf -i $(find | grep '\.py$')
VERSION_CONTROL=none indent -kr -nut -l180 $(find | grep '\.c$')

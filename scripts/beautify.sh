#!/usr/bin/bash

isort $(find | grep '\.py$')
yapf -i $(find | grep '\.py$')

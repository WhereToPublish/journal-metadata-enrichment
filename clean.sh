#!/usr/bin/env bash
rm -rf agent/output/
cd WhereToPublish.github.io
git checkout main
git fetch origin
git reset --hard origin/main
# checkout the branch data (hard reset to discard any local changes, then pull the latest from origin)
git checkout data
git reset --hard origin/data
cd ..
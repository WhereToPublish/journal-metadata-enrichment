#!/usr/bin/env bash
rm -rf agent/output/
cd WhereToPublish.github.io
# checkout the branch data (hard reset to discard any local changes, then pull the latest from origin)
git checkout data
git reset --hard origin/data
cd ..
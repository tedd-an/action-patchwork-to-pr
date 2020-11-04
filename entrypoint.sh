#!/bin/bash

set -e

if [ -z "$GITHUB_TOKEN" ]; then
	echo "Set GITHUB_TOKEN environment variable"
	exit 1
fi

PW_URL=$1
PW_EXCLUDE_STR=$2
BASE_BRANCH=$3

echo "PW_URL = $PW_URL"
echo "PW_EXCLUDE_STR = $PW_EXCLUDE_STR"
echo "BASE_BRANCH = $BASE_BRANCH"

git config user.name "$GITHUB_ACTOR"
git config user.email "$GITHUB_ACTOR@users.noreply.github.com"

git remote set-url origin "https://x-access-token:$GITHUB_TOKEN@github.com/$GITHUB_REPOSITORY"
git branch -a
git remote -v

export HUB_VERBOSE=1
export HUB_PROTOCOL=https
export GITHUB_USER="$GITHUB_ACTOR"

echo "HUB_PROTOCOL=$HUB_PROTOCOL"
echo "GITHUB_USER=$GITHUB_USER"

echo "############"
/pwclient-save-series.py -u $PW_URL -e $PW_EXCLUDE_STR -d /series
echo "############"
/create-pull-request.py -r $GITHUB_REPOSITORY -b $BASE_BRANCH -s /series

#!/usr/bin/env bash
mkdir -p logs
DATE=$(date -Iseconds)
STDOUT_LOG="logs/stdout-$DATE.log"
STDERR_LOG="logs/stderr-$DATE.log"
touch $STDOUT_LOG $STDERR_LOG
ln -f -s $PWD/$STDOUT_LOG $PWD/logs/stdout-latest.log
ln -f -s $PWD/$STDERR_LOG $PWD/logs/stderr-latest.log
$@ > $STDOUT_LOG 2> $STDERR_LOG &

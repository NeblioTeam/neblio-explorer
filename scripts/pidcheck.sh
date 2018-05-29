#!/bin/bash

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

file="$SCRIPTPATH/../tmp/index.pid"
if [ -f $file ]
  then
   name=$(cat "$file")

  if [ -z "${kpid}" -a -d "/proc/${kpid}" ]
    then
      rm $file
  fi
fi

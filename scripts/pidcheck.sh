#!/bin/bash

file="$PWD/tmp/index.pid"
if [ -f $file ]
then
  if ! [ -s $file ]
  then
    # file empty
    exit 0
  else
    kpid=$(cat "$file")
    if ! [ -n "${kpid}" -a -d "/proc/${kpid}" ]
    then
      rm $file
    fi
  fi
fi
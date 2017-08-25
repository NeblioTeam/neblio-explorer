#!/bin/bash

file="/home/explorer/tmp/index.pid"
if [ -f $file ]
  then
   name=$(cat "$file")

  if [ -z "${kpid}" -a -d "/proc/${kpid}" ]
    then
      rm /home/explorer/tmp/index.pid
  fi
fi

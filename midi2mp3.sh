#!/bin/bash
# rm output file if exists
[ -e $4 ] && rm $4
# Usage: midi2mp3.sh <Tempo> <Audio duration in sec> <midi file> <out mp3 file>
timidity -c $5 -p 128 --config-file=./timidity.cfg -T $1 $3 -Ow -o  - |  ffmpeg -i - -acodec libmp3lame -ss 0 -to $2 -ab 256k $4 &> /dev/null


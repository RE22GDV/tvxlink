#!/bin/zsh
# Repeat the decompilation of NetUpdate 4.35, NetUpdate 3.16 and RM-404 (Ghidra 12, headless).
#   run_ghidra.sh <folder with NetUpdate4.exe NetUpdate3.exe RM404.exe> <output folder>
# Needs: brew install ghidra (pulls openjdk@25). Each program takes 10–15 minutes.
set -e
SRC=${1:?folder with the exe files}; OUT=${2:?output folder}
HERE=$(cd "$(dirname "$0")" && pwd)
export JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home}
AH=${GHIDRA_HEADLESS:-/opt/homebrew/opt/ghidra/libexec/support/analyzeHeadless}
CMDS='^\*[?A-Z]|EXPLORER-II|XOFF|3410'

# Key functions (addresses from the notes, see reverse/FULL_REPORT.uk.md)
NU4=0x4a1cd0,0x4a08b0,0x4a0780,0x4a05e0,0x41d610,0x41db10,0x41de40,0x41e170,0x41e4a0,0x41eaa0,0x475270,0x47b420,0x48f6a0,0x491d60,0x47b110,0x5b4a20,0x5bab90,0x5b52a0,0x5afd20,0x5b6e30,0x5b9350
NU3=0x43a1c0,0x43cbd0,0x43db00,0x43de50,0x43e0a0,0x43e2b0,0x48f770,0x463130
RM=0x4ac2c0,0x4ac3a8,0x4ac3f8,0x4ac448,0x4ac640,0x4ac76c,0x4ac964,0x4adda0,0x4ade94,0x4adf34,0x4ae31c,0x445415,0x453924,0x4539ac,0x487da0,0x487dd8

run() {  # name exe addresses
  local name=$1 exe=$2 addrs=$3
  mkdir -p "$OUT/$name" "$OUT/_proj"
  "$AH" "$OUT/_proj" "$name" -import "$SRC/$exe" -overwrite -scriptPath "$HERE/ghidra" \
    -postScript FuncMap.java "$OUT/$name/funcmap.tsv" \
    -postScript Decomp.java "$OUT/$name/key_functions.c" "$addrs" \
    -postScript DumpCmdFuncs.java "$OUT/$name/command_functions.c" "$CMDS" > "$OUT/$name/ghidra.log" 2>&1
  echo "$name done"
}
run NetUpdate4 NetUpdate4.exe $NU4 &
run NetUpdate3 NetUpdate3.exe $NU3 &
run RM404 RM404.exe $RM &
wait

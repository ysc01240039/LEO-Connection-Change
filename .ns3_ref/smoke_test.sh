#!/bin/bash
# Smoke test: timing + output check for leo_cbdp_eval at N=200
cd ~/ns-3-dev
for p in Dijkstra CBDP OLSR AODV; do
  echo "== $p =="
  /usr/bin/time -f 'WALL %e s' ./ns3 run "leo_cbdp_eval --mode=compare --protocol=$p --nSats=200 --nCores=60 --simTime=40 --flowStart=5 --seed=42" 2>&1 | tail -14
done

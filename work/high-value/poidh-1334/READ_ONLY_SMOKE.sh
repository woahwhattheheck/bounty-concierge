#!/usr/bin/env bash
set -euo pipefail

: "${BASE_SEPOLIA_RPC_URL:?set BASE_SEPOLIA_RPC_URL to a read-only Base Sepolia RPC endpoint}"
CONTRACT="${POAP_CONTRACT:-0xC3249356a483fbe17d5355D39105D2eA666d9de6}"

command -v cast >/dev/null 2>&1 || {
  echo "cast (Foundry) is required" >&2
  exit 127
}

chain_id="$(cast chain-id --rpc-url "$BASE_SEPOLIA_RPC_URL")"
[[ "$chain_id" == "84532" ]] || {
  echo "refusing non-Base-Sepolia chain id: $chain_id" >&2
  exit 2
}

code="$(cast code "$CONTRACT" --rpc-url "$BASE_SEPOLIA_RPC_URL")"
[[ "$code" != "0x" ]] || {
  echo "no contract bytecode at $CONTRACT" >&2
  exit 3
}

total="$(cast call "$CONTRACT" 'totalEvents()(uint256)' --rpc-url "$BASE_SEPOLIA_RPC_URL")"
event0="$(cast call "$CONTRACT" 'events(uint256)(string,string,uint256,string,bytes32,address,address,uint256,string,bool,bool)' 0 --rpc-url "$BASE_SEPOLIA_RPC_URL")"
multi0="$(cast call "$CONTRACT" 'getMultichainEventId(uint256)(string)' 0 --rpc-url "$BASE_SEPOLIA_RPC_URL")"
uri0="$(cast call "$CONTRACT" 'uri(uint256)(string)' 0 --rpc-url "$BASE_SEPOLIA_RPC_URL")"

printf 'chain_id=%s\ncontract=%s\ntotalEvents=%s\n' "$chain_id" "$CONTRACT" "$total"
printf 'event0=%s\n' "$event0"
printf 'multichain0=%s\n' "$multi0"
case "$multi0" in
  *"eip155:84532:"*) ;;
  *) echo "unexpected CAIP-2 identifier: $multi0" >&2; exit 4 ;;
esac
case "$uri0" in
  data:application/json\;base64,*) ;;
  *) echo "unexpected ERC1155 uri prefix" >&2; exit 5 ;;
esac

echo "read-only smoke: PASS"

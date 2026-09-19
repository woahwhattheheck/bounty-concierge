# PayD #480 source baseline

Pinned current upstream main: `af5c348e83033ed3340e589b68e8554f0303060e`.

The repository already mounts Socket.IO and a global SocketProvider.
TransactionHistory currently uses REST history and does not consume the socket.
The backend transaction-update helper has no observed repository call site
outside its definition. Reconnect transport exists, but desired transaction
subscriptions are not retained and restored after reconnect.

Recommended assigned scope: wire publication to durable status changes, restore
subscriptions after reconnect, reconcile REST history after realtime events,
add the requested header connection indicator, and add focused tests.

GrantFox was Unassigned with Apply enabled when checked. This is pre-assignment
research only; refresh assignment and source before upstream implementation.

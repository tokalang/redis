# `official/redis`

Official opt-in Redis client package for Toka. Package version `0.2.0` is the
first standalone release line. This document describes API profile v1.

Status: **bounded RESP2 codec, serial TCP/TLS client, ordered pipelines, and bounded connection pooling**.

`official/redis` is an optional pure-Toka Redis client package. It encodes
binary-safe RESP2 commands, incrementally decodes one response from an owned
`Vec<u8>` buffer, and supports serial plaintext or TLS connections directly
or through an exclusive pool lease.

```toka
import official/redis::{RedisClient, RedisCommand}

auto client# = RedisClient::connect_async(cede address, 5000).await!
auto value = client#.get_async("session:42").await!
```

The codec returns `RedisDecode::NeedMore()` without consuming an incomplete
frame. A completed bulk string is returned as owned `Bytes`, never a view into
the mutable receive buffer. Any I/O failure, EOF, timeout, cancellation, or
malformed reply after a command write closes the client; it cannot be reused.
Extra reply bytes observed with the matched reply are rejected: a serial
client must not assign an unsolicited buffered reply to a later command.

`connect_tls_async` verifies the peer and SNI hostname against the system
trust store. `connect_tls_with_ca_file_async` is available for private CA
deployments. `connect_insecure_tls_for_test_async` is deliberately named for
deterministic test fixtures only; it is never a fallback from verified TLS.

`RedisPipeline` batches commands into one write and returns replies in command
order. It reads exactly one RESP value per command; transport, decoder, or
reply-count failure poisons the connection rather than leaving an ambiguous
reply for the next operation.

```toka
auto pipeline# = RedisPipeline::new()
pipeline#.push(cede RedisCommand::new("PING"))
pipeline#.push(cede RedisCommand::new("INFO"))
auto replies = client#.execute_pipeline_async(cede pipeline).await!
```

`get_async`, `set_async`, and `del_async` are thin wrappers over the serial
`execute_async` primitive. `GET` returns `Ok(None)` for a missing key;
`SET` accepts an owned binary `Bytes` value; `DEL` returns Redis's deletion
count. A well-formed Redis `-ERR` reply becomes a `RedisError(kind = "server")`
for these typed operations. Use `execute_async` when the application needs the
raw `RedisValue` reply.

RESP3, Pub/Sub, cluster, Sentinel, retry policy, and Redis Cluster routing
remain outside this package slice.

## Pooling

`RedisPool` owns a bounded set of plaintext or verified-TLS clients. An
acquired `RedisLease` is the only mutable owner of one client, so it preserves
the serial request/reply contract across `.await`; dropping the lease returns a
healthy client automatically.

```toka
auto pool# = RedisPool::new(cede address, 5000, 16).unwrap()
{
    auto lease# = pool#.acquire_async(1000).await!
    lease#.get_async("session:42").await!
}
pool#.close()
```

`new_tls` and `new_tls_with_ca_file` retain the same verified-TLS choices as
the direct client. `close()` stops new leases, drains idle sockets, and causes
checked-out leases to close on return. A capacity timeout or cancellation
leaves the pool unchanged. A client is discarded rather than returned after
I/O, cancellation, decode/protocol failure, or an unread/extra pipeline reply.

## Qualification

The required qualification toolchain is the published Toka `v1.0.0-rc.5` SDK.
Install OpenSSL, pkg-config, and Clang, then provide either an installed SDK
explicitly:

```sh
TOKA=/path/to/bin/toka \
TOKAC=/path/to/bin/tokac \
TOKA_LIB=/path/to/lib \
python3 tests/qualify_package.py
```

or a Toka source checkout whose `build/bin/toka`, `build/bin/tokac`, and
`lib/sys/toka_rt.o` have already been built:

```sh
TOKA_ROOT=/path/to/toka python3 tests/qualify_package.py
```

Qualification builds and runs all five deterministic codec, ownership, client,
TLS/pipeline, and pool suites. It generates a one-run localhost certificate and
private key only inside its temporary package copy; no test private key is
tracked. It then performs a first local path fetch, replays the resulting lock
with `TOKA_OFFLINE=1`, and builds and runs an isolated public-import consumer.

Real-service compatibility is a separate fail-closed Docker qualification. It
verifies Redis 7.4.x and 8.2.x with password TCP and private-CA TLS; a
successful local Docker run is maintainer evidence, while Linux CI records the
release-gate artifact. A runner that cannot publish loopback ports is reported
as `not-run`, never as a passing package test.

```sh
TOKAC=/path/to/bin/tokac \
TOKA_LIB=/path/to/lib \
python3 tests/qualify_real_service.py \
  --report build/redis-real-service.json
```

The runner compiles this checkout's `tests/real_service_v1.tk` and
`tests/clone_ownership_v1.tk`, then tests the `redis:7.4-alpine` and
`redis:8.2-alpine` images. Missing compiler, runtime, OpenSSL, Docker, or
loopback publication writes a `status: not-run` report and exits 2. A failed
compatibility check writes `status: failed` and exits 1; only the complete
matrix writes `status: passed` and exits 0.

Qualification on CI is executed on **Linux x64 (`ubuntu-22.04`)** and
**macOS arm64 (`macos-15`)**.

## Repository migration

This repository is the standalone cutover candidate. Until the release, catalog
consumer replay, and Toka-root removal complete,
[`tokalang/toka/official/redis`](https://github.com/tokalang/toka/tree/main/official/redis)
remains authoritative.

Cutover will be one-way. The compiler repository copy will be removed only after
the standalone CI is green, the `v0.2.0` release is published, the verified
catalog entry is registered, and existing consumers have moved to the released,
locked package; this repository will not become a long-lived mirror or
submodule.

The history was imported with `git subtree split` from
`tokalang/toka@07d86771cc5b28d73f75e8ab560284315a904685`, path
`official/redis`. The last source commit touching that path is
`0eb95497662c6588f439f5279ee5c8f5a3333ae1`; the split history tip is
`fa61ee4ec85b458afa8aeae55b66c7caac7af21f`.

## License

Apache License 2.0. See [LICENSE](LICENSE).

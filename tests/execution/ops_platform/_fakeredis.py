"""Minimal Redis-compatible test double.

Implements only the subset of redis-py methods used by the Phase 9
coordination modules: set/get/del/expire/ttl/incr/eval (subset),
xadd/xread/xreadgroup/xack/xrange/xlen/xpending/xinfo_groups/xgroup_create,
hset/hgetall/scan_iter/sadd/srem/smembers/delete/pipeline/keys.

NOT a complete Redis emulator. Just enough for the unit tests to exercise
the production coordination protocol against a controllable in-memory backend.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict


class FakePipeline:
    def __init__(self, client):
        self._client = client
        self._ops = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def incr(self, key, amount=1):
        self._ops.append(("incr", key, amount))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "incr":
                out.append(self._client.incr(op[1], op[2]))
            elif op[0] == "expire":
                out.append(self._client.expire(op[1], op[2]))
        self._ops.clear()
        return out


class FakeRedis:
    """Subset Redis emulator. Keys + streams + hashes + sets."""

    def __init__(self):
        self._kv: dict[str, str] = {}
        self._expirations: dict[str, float] = {}
        self._streams: dict[str, list] = OrderedDict()    # name -> list[(id, fields)]
        self._stream_groups: dict[str, dict] = {}         # name -> {group: {last_id, pending: {id: consumer}}}
        self._hashes: dict[str, dict] = {}
        self._sets: dict[str, set] = {}

    # ── strings ─────────────────────────────────────────────────────

    def set(self, key, value, nx=False, ex=None):
        self._purge(key)
        if nx and key in self._kv:
            return False
        self._kv[key] = str(value)
        if ex is not None:
            self._expirations[key] = time.time() + ex
        return True

    def get(self, key):
        self._purge(key)
        return self._kv.get(key)

    def delete(self, *keys):
        n = 0
        for k in keys:
            self._purge(k)
            if k in self._kv:
                del self._kv[k]; n += 1
            elif k in self._hashes:
                del self._hashes[k]; n += 1
            elif k in self._sets:
                del self._sets[k]; n += 1
            elif k in self._streams:
                del self._streams[k]; n += 1
        return n

    def expire(self, key, seconds):
        if key in self._kv or key in self._hashes:
            self._expirations[key] = time.time() + seconds
            return 1
        return 0

    def ttl(self, key):
        self._purge(key)
        if key not in self._kv and key not in self._hashes:
            return -2
        exp = self._expirations.get(key)
        if exp is None:
            return -1
        return max(0, int(exp - time.time()))

    def incr(self, key, amount=1):
        self._purge(key)
        current = int(self._kv.get(key, "0"))
        current += amount
        self._kv[key] = str(current)
        return current

    def eval(self, script, num_keys, *args):
        keys = args[:num_keys]
        script_args = args[num_keys:]
        # Two known scripts: release + heartbeat (both check value == arg[0])
        if "del" in script:
            key = keys[0]
            if self.get(key) == str(script_args[0]):
                self.delete(key)
                return 1
            return 0
        if "expire" in script:
            key = keys[0]
            if self.get(key) == str(script_args[0]):
                self.expire(key, int(script_args[1]))
                return 1
            return 0
        return 0

    def keys(self, pattern):
        # Only supports "prefix*" patterns used by reset paths.
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in list(self._kv.keys()) + list(self._hashes.keys())
                       + list(self._sets.keys()) + list(self._streams.keys())
                       if k.startswith(prefix)]
        return [k for k in self._kv if k == pattern]

    # ── streams ─────────────────────────────────────────────────────

    def xadd(self, name, fields, maxlen=None, approximate=True):
        msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        self._streams.setdefault(name, []).append((msg_id, dict(fields)))
        if maxlen is not None and len(self._streams[name]) > maxlen:
            self._streams[name] = self._streams[name][-maxlen:]
        return msg_id

    def xlen(self, name):
        return len(self._streams.get(name, []))

    def xrange(self, name, min="-", max="+", count=None):
        rows = self._streams.get(name, [])
        if count:
            rows = rows[:count]
        return list(rows)

    def xgroup_create(self, name, group, id="$", mkstream=False):
        if mkstream:
            self._streams.setdefault(name, [])
        self._stream_groups.setdefault(name, {})
        if group in self._stream_groups[name]:
            raise Exception("group exists")
        # id="0" → start of stream (replay all). id="$" → tail (new only).
        start_index = 0 if id == "0" else len(self._streams.get(name, []))
        self._stream_groups[name][group] = {"last_index": start_index,
                                                "pending": {}}

    def xreadgroup(self, group, consumer, streams, count=None, block=None):
        out = []
        for stream_key, _start in streams.items():
            sg = self._stream_groups.setdefault(stream_key, {})
            if group not in sg:
                sg[group] = {"last_index": 0, "pending": {}}
            entries = self._streams.get(stream_key, [])
            start_idx = sg[group]["last_index"]
            limit = count or 10
            picked = entries[start_idx:start_idx + limit]
            if not picked:
                continue
            for msg_id, fields in picked:
                sg[group]["pending"][msg_id] = consumer
            sg[group]["last_index"] = start_idx + len(picked)
            out.append((stream_key, [(mid, fields) for mid, fields in picked]))
        return out

    def xack(self, stream_key, group, *ids):
        sg = self._stream_groups.get(stream_key, {}).get(group, {})
        n = 0
        for mid in ids:
            if mid in sg.get("pending", {}):
                del sg["pending"][mid]
                n += 1
        return n

    def xpending(self, stream_key, group):
        sg = self._stream_groups.get(stream_key, {}).get(group, {})
        return {"pending": len(sg.get("pending", {}))}

    def xinfo_groups(self, stream_key):
        out = []
        for gname, g in self._stream_groups.get(stream_key, {}).items():
            out.append({"name": gname,
                          "pending": len(g.get("pending", {})),
                          "last-delivered-id": str(g.get("last_index", 0))})
        return out

    # ── hashes ───────────────────────────────────────────────────────

    def hset(self, key, mapping=None, **kwargs):
        if mapping is None:
            mapping = kwargs
        self._hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})
        return len(mapping)

    def hgetall(self, key):
        self._purge(key)
        return dict(self._hashes.get(key, {}))

    # ── sets ─────────────────────────────────────────────────────────

    def sadd(self, key, *members):
        s = self._sets.setdefault(key, set())
        added = 0
        for m in members:
            if m not in s:
                s.add(m); added += 1
        return added

    def srem(self, key, *members):
        s = self._sets.get(key, set())
        removed = 0
        for m in members:
            if m in s:
                s.remove(m); removed += 1
        return removed

    def smembers(self, key):
        return set(self._sets.get(key, set()))

    def scan_iter(self, match=None, count=None):
        # Supports prefix + "*" patterns
        if match and match.endswith("*"):
            prefix = match[:-1]
            for k in list(self._hashes.keys()) + list(self._kv.keys()):
                if k.startswith(prefix):
                    yield k
        elif match:
            for k in self._hashes:
                if k == match:
                    yield k

    # ── pipeline ─────────────────────────────────────────────────────

    def pipeline(self):
        return FakePipeline(self)

    # ── publish/subscribe (no-op for unit tests) ─────────────────────

    def publish(self, channel, message):
        return 0

    # ── Internal ─────────────────────────────────────────────────────

    def _purge(self, key):
        exp = self._expirations.get(key)
        if exp is not None and exp <= time.time():
            self._expirations.pop(key, None)
            self._kv.pop(key, None)
            self._hashes.pop(key, None)

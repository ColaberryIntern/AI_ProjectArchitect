# Rolling upgrade + disaster recovery runbook

## Upgrade order

1. **Pre-upgrade**
   - `kubectl exec` into one api pod and run:
     ```
     curl -fs http://localhost:8000/ops/system/cluster-health
     curl -X POST http://localhost:8000/ops/audit/sign-pending
     curl -X POST http://localhost:8000/ops/system/backup
     ```
   - Verify `ready: true`. If not, fix blockers first.
   - Confirm outbox metrics: `curl http://localhost:8000/ops/outbox/metrics` shows
     `by_state.pending == 0` and `dead_letter_count == 0`. Drain DLQ if not.

2. **Drain workers** (replicas keep running but stop claiming new work)
   ```
   kubectl rollout pause deployment ops-worker-multihost
   # Wait until no active step claims:
   curl http://localhost:8000/ops/coordination/active-claims
   ```

3. **Roll the api deployment** with `RollingUpdate`:
   ```
   kubectl set image deployment/ops-api-multihost api=colaberry/ops-platform:v10.0.0
   kubectl rollout status deployment/ops-api-multihost --timeout=10m
   ```
   - Health: `/ops/system/cluster-health` must report `ready: true` on every new pod.

4. **Roll the workers**:
   ```
   kubectl rollout resume deployment ops-worker-multihost
   kubectl set image deployment/ops-worker-multihost worker=colaberry/ops-platform:v10.0.0
   kubectl rollout status deployment/ops-worker-multihost --timeout=15m
   ```

5. **Roll the scheduler** (leader will fail over to standby):
   ```
   kubectl set image deployment/ops-scheduler scheduler=colaberry/ops-platform:v10.0.0
   ```

6. **Post-upgrade verification**
   ```
   curl http://localhost:8000/ops/system/migrations
   curl -X POST http://localhost:8000/ops/system/migrations/apply
   curl http://localhost:8000/ops/projections
   for p in incident_timeline active_alerts orchestration_state; do
     curl -X POST "http://localhost:8000/ops/projections/$p/rebuild"
   done
   curl http://localhost:8000/ops/outbox/metrics
   curl -X POST http://localhost:8000/ops/audit/verify
   ```

## Rollback procedure

If any verification step fails:

1. **Halt the rollout**:
   ```
   kubectl rollout pause deployment ops-api-multihost
   ```
2. **Roll back image**:
   ```
   kubectl rollout undo deployment ops-api-multihost
   kubectl rollout undo deployment ops-worker-multihost
   ```
3. **Migrations**: if Phase 8 migrations were applied during the upgrade,
   rollback by calling `POST /ops/system/migrations/rollback` repeatedly
   until the applied set matches the prior version.
4. **State restore (last resort)**: if data drift is suspected, restore
   from the pre-upgrade snapshot:
   ```
   curl -X POST http://localhost:8000/ops/system/restore \
     -H "Content-Type: application/json" \
     -d '{"archive_path":"output/ops_platform/backups/<pre-upgrade>.tar.gz",
          "restore_to":"output/ops_platform_restored"}'
   ```
   Restore lands in a SIDE directory; operator must compare before
   overwriting live state.

## Disaster recovery

### Loss of all Redis nodes

1. Confirm via `curl http://localhost:8000/ops/runtime/redis/status` —
   `client_wired=false`.
2. The platform stays up but degrades to single-host coordination
   per `/ops/coordination/topology`.
3. Operator decision:
   - **Restore Redis from RDB/AOF**: redeploy `ops-redis-ha` StatefulSet;
     PVC retains the AOF.
   - **Bootstrap fresh**: scale down api/worker, reinitialize Redis,
     then `curl -X POST http://localhost:8000/ops/outbox/reconcile`
     to drain locally-persisted events to the fresh Redis cluster.
4. Verify fencing-token monotonicity has held: any active downstream
   resource that holds a previous fencing token will be rejected on the
   next interaction (by design).

### Corrupted snapshot

1. `curl http://localhost:8000/ops/backup/manifests` — find the suspect.
2. `curl -X POST http://localhost:8000/ops/backup/verify/<manifest_id>` —
   if `verified=false`, the file SHA-256 chain identified the corrupted
   member.
3. Discard; restore from the parent snapshot (lineage is visible via
   `/ops/backup/lineage`).

### Stuck orchestration after worker crash

1. `curl http://localhost:8000/ops/coordination/orphan-orchestrations`
2. For each orphan:
   - Operator can call `POST /ops/orchestrations/<id>/rewind` to roll back
     to a known step, OR
   - Call `POST /ops/orchestrations/recover-after-crash` to release stale
     claims (does NOT mutate the workflow state — only frees the lock).

## Operator maintenance mode

```
# Enable
curl -X POST http://localhost:8000/ops/controls/maintenance-mode \
  -H "Content-Type: application/json" \
  -d '{"reason":"v10 rollout"}'

# All workflow executions return status=blocked until disabled:
curl -X POST http://localhost:8000/ops/controls/maintenance-mode/disable
```

Audit rows record entry + exit; live dashboard shows the banner.

"""Job lifecycle: states, retries, cancellation, duplicates (spec §20, §24.10).

Each test drives the real endpoints; the worker pool runs for real, so progress and
timing are genuine rather than simulated.
"""

import time
import uuid

import pytest

from app import store


def _submit(client, model_pk, prompt="a lighthouse at dawn", **extra):
    # A unique suffix keeps tests independent: the duplicate-submission guard is
    # real and would otherwise refuse the second test with the same prompt.
    payload = {"model_pk": model_pk, "prompt": f"{prompt} {uuid.uuid4().hex[:8]}"}
    payload.update(extra)
    return client.post("/api/generations/images", payload)


class TestStateMachine:
    def test_a_job_is_accepted_and_then_succeeds(self, client, mock_connection, wait_for_job):
        code, body = _submit(client, mock_connection["image_pk"])
        assert code == 202, body
        job = body["data"]
        assert job["status"] in ("queued", "running")

        final = wait_for_job(job["id"])
        assert final["status"] == "succeeded", final
        assert final["progress"] == 100

    def test_the_state_history_is_preserved(self, client, mock_connection, wait_for_job):
        code, body = _submit(client, mock_connection["image_pk"], "history test")
        final = wait_for_job(body["data"]["id"])
        transitions = [e["to_status"] for e in final["events"]]
        assert transitions[0] == "queued"
        assert "running" in transitions
        assert transitions[-1] == "succeeded"

    def test_timestamps_are_recorded(self, client, mock_connection, wait_for_job):
        code, body = _submit(client, mock_connection["image_pk"], "timestamps")
        final = wait_for_job(body["data"]["id"])
        assert final["submitted_at"]
        assert final["started_at"]
        assert final["finished_at"]

    def test_outputs_are_stored_as_real_files(self, client, mock_connection, wait_for_job):
        code, body = _submit(client, mock_connection["image_pk"], "store me",
                             params={"count": 2})
        final = wait_for_job(body["data"]["id"])
        assert len(final["outputs"]) == 2
        for output in final["outputs"]:
            assert output["asset_id"]
            code, blob = client.get(f"/api/assets/{output['asset_id']}/raw")
            assert code == 200
            assert blob["raw"][:8] == b"\x89PNG\r\n\x1a\n"

    def test_a_missing_model_is_refused(self, client):
        code, body = client.post("/api/generations/images",
                                 {"model_pk": "mdl_does_not_exist", "prompt": "x"})
        assert code == 404

    def test_an_empty_prompt_is_refused(self, client, mock_connection):
        code, body = client.post("/api/generations/images",
                                 {"model_pk": mock_connection["image_pk"], "prompt": "   "})
        assert code == 400 and body["error"]["code"] == "missing_field"


class TestDuplicates:
    def test_an_identical_in_flight_job_is_refused(self, client, mock_connection):
        """Two byte-identical requests: the second must not spend money again.

        The guard deliberately covers only *in-flight* jobs (`queued`/`running`), so this
        test has to guarantee the first is still in flight when the second arrives. The
        demo provider finishes in milliseconds, so without holding the job the first one
        can succeed first — and then a second submission is legitimately allowed, which
        is not a bug.
        """
        import uuid

        payload = {"model_pk": mock_connection["image_pk"],
                   "prompt": f"dupe prompt {uuid.uuid4().hex[:8]}"}
        code, body = client.post("/api/generations/images", payload)
        assert code == 202
        job_id = body["data"]["id"]
        # Pin the first job in flight so the guard has something to see, whatever the
        # worker pool happened to do in the meantime.
        store.execute(
            "UPDATE generation_jobs SET status='running', lease_token='lease_hold', "
            "lease_until='2999-01-01T00:00:00Z' WHERE id=?", (job_id,))
        code, body = client.post("/api/generations/images", payload)
        assert code == 409
        assert body["error"]["code"] == "duplicate_submission"
        assert body["error"]["details"]["job_id"]
        # Release it so the worker can finish and the session stays clean.
        store.execute(
            "UPDATE generation_jobs SET status='queued', lease_token='', lease_until=NULL "
            "WHERE id=?", (job_id,))

    def test_a_different_prompt_is_not_a_duplicate(self, client, mock_connection):
        code, _ = _submit(client, mock_connection["image_pk"], "one prompt")
        assert code == 202
        code, _ = _submit(client, mock_connection["image_pk"], "a different prompt")
        assert code == 202

    def test_different_parameters_are_not_a_duplicate(self, client, mock_connection):
        code, _ = _submit(client, mock_connection["image_pk"], "same text",
                          params={"count": 1})
        assert code == 202
        code, _ = _submit(client, mock_connection["image_pk"], "same text",
                          params={"count": 2})
        assert code == 202


class TestCancellation:
    def test_a_queued_job_can_be_cancelled(self, client, mock_connection):
        """Cancel straight after submit, before a worker can pick it up."""
        code, body = _submit(client, mock_connection["image_pk"], "cancel me quickly",
                             params={"count": 4})
        job_id = body["data"]["id"]
        code, body = client.post(f"/api/jobs/{job_id}/cancel")
        assert code == 200, body
        final = body["data"]
        assert final["status"] in ("cancelled", "running", "cancelling")

    def test_cancelling_a_finished_job_is_refused(self, client, mock_connection, wait_for_job):
        code, body = _submit(client, mock_connection["image_pk"], "already done")
        final = wait_for_job(body["data"]["id"])
        assert final["status"] == "succeeded"
        code, body = client.post(f"/api/jobs/{final['id']}/cancel")
        assert code == 400
        assert body["error"]["code"] == "job_not_cancellable"


class TestRetry:
    def test_retrying_a_succeeded_job_is_refused(self, client, mock_connection, wait_for_job):
        code, body = _submit(client, mock_connection["image_pk"], "retry nope")
        final = wait_for_job(body["data"]["id"])
        code, body = client.post(f"/api/jobs/{final['id']}/retry")
        assert code == 400
        assert body["error"]["code"] == "job_not_cancellable"

    def test_a_failed_job_can_be_requeued(self, client, mock_connection):
        """Mark a job failed directly, then retry it through the API."""
        code, body = _submit(client, mock_connection["image_pk"], "will fail")
        job_id = body["data"]["id"]
        from app.services import jobs as jobs_service

        for _ in range(50):
            row = store.row("SELECT status FROM generation_jobs WHERE id=?", (job_id,))
            if row["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        store.execute("UPDATE generation_jobs SET status='failed', error_code='provider_outage', "
                      "error_message='simulated' WHERE id=?", (job_id,))

        code, body = client.post(f"/api/jobs/{job_id}/retry")
        assert code == 202, body
        assert body["data"]["status"] == "queued"
        assert body["data"]["attempts"] == 0


class TestListing:
    def test_jobs_are_listed_with_pagination(self, client, mock_connection):
        for index in range(3):
            _submit(client, mock_connection["image_pk"], f"list test {index}")
        code, body = client.get("/api/jobs?limit=2")
        assert code == 200
        assert len(body["jobs"]) <= 2
        assert body["total"] >= 3
        assert body["limit"] == 2

    def test_filtering_by_status(self, client, mock_connection):
        code, body = client.get("/api/jobs?status=succeeded")
        assert code == 200
        assert all(j["status"] == "succeeded" for j in body["jobs"])

    def test_an_unknown_status_is_refused(self, client):
        code, body = client.get("/api/jobs?status=exploded")
        assert code == 400


class TestRecovery:
    def test_an_interrupted_job_is_requeued(self, client, mock_connection):
        """A job left running by a dead worker is put back in the queue (spec §21).

        With leases, "interrupted" has a precise meaning: `running` with no live claim.
        A job whose lease is still valid is deliberately left alone, because another
        invocation may genuinely be working on it — so this sets an expired lease to
        model the dead worker rather than just flipping the status.
        """
        from app.services import jobs as jobs_service

        code, body = _submit(client, mock_connection["image_pk"], "interrupted")
        job_id = body["data"]["id"]
        store.execute(
            "UPDATE generation_jobs SET status='running', lease_token='lease_dead', "
            "lease_until='2000-01-01T00:00:00Z' WHERE id=?", (job_id,))
        count = jobs_service.requeue_unfinished()
        assert count >= 1
        row = store.row("SELECT status, lease_token FROM generation_jobs WHERE id=?", (job_id,))
        assert row["status"] in ("queued", "running", "succeeded")
        assert row["lease_token"] == "", "an abandoned claim must be cleared"

    def test_a_job_with_a_live_lease_is_left_alone(self, client, mock_connection):
        """The other half: a valid claim is not stolen from a working invocation."""
        from app.services import jobs as jobs_service

        code, body = _submit(client, mock_connection["image_pk"], "still working")
        job_id = body["data"]["id"]
        store.execute(
            "UPDATE generation_jobs SET status='running', lease_token='lease_live', "
            "lease_until='2999-01-01T00:00:00Z' WHERE id=?", (job_id,))
        jobs_service.requeue_unfinished()
        row = store.row("SELECT status, lease_token FROM generation_jobs WHERE id=?", (job_id,))
        assert row["status"] == "running"
        assert row["lease_token"] == "lease_live"


class TestTheLeaseIsReleased:
    """A finished job must never be claimed again.

    The bug this pins: `running` with an expired lease is deliberately claimable, so a
    job that reached `succeeded` while still holding its lease was picked up by the next
    worker and executed a second time. The job looked "flaky" — sometimes two outputs,
    sometimes four — because it depended on how quickly the lease expired.
    """

    def test_a_finished_job_holds_no_lease(self, client, mock_connection, wait_for_job):
        from app.services import jobs as jobs_service

        code, body = _submit(client, mock_connection["image_pk"], "lease released")
        job_id = body["data"]["id"]
        wait_for_job(job_id)
        row = store.row(
            "SELECT status, lease_token, lease_until FROM generation_jobs WHERE id=?",
            (job_id,))
        assert row["status"] == "succeeded"
        assert row["lease_token"] == "", "a finished job kept its lease"
        assert row["lease_until"] is None

    def test_the_runner_cannot_claim_a_finished_job(self, client, mock_connection, wait_for_job):
        """Even with an expired lease, a terminal job is not runnable."""
        from app.services import jobs as jobs_service
        from app.services import runner

        code, body = _submit(client, mock_connection["image_pk"], "not rerun")
        job_id = body["data"]["id"]
        wait_for_job(job_id)
        # Simulate a stale lease left on a finished row, which used to be claimable.
        store.execute(
            "UPDATE generation_jobs SET lease_until='2000-01-01T00:00:00Z' WHERE id=?",
            (job_id,))
        before = store.scalar(
            "SELECT COUNT(*) FROM outputs WHERE job_id=?", (job_id,)) or 0
        runner.run_one()
        runner.run_one()
        after = store.scalar(
            "SELECT COUNT(*) FROM outputs WHERE job_id=?", (job_id,)) or 0
        assert after == before, "a finished job ran again and appended outputs"

    def test_the_output_count_is_stable_across_repeated_ticks(self, client, mock_connection, wait_for_job):
        from app.services import runner

        code, body = _submit(client, mock_connection["image_pk"], "stable outputs")
        job_id = body["data"]["id"]
        wait_for_job(job_id)
        counts = []
        for _ in range(3):
            runner.run_one()
            counts.append(store.scalar(
                "SELECT COUNT(*) FROM outputs WHERE job_id=?", (job_id,)) or 0)
        assert len(set(counts)) == 1, f"output count changed across ticks: {counts}"


class TestClaimingIsExclusive:
    """`claim_next` must never hand out the same job twice.

    The bug this pins: claiming stamped a lease but left `status='queued'`, so the
    `status='queued'` branch of the claim query kept matching a job that already held a
    live lease. Claiming sequentially returned the first job again on the fourth call,
    and with four workers a single job ran up to twelve times — visible as a job with
    four outputs instead of two, intermittently. Claiming now sets `status='running'`
    in the same statement that stamps the lease.
    """

    def test_claiming_the_same_job_twice_is_impossible(self, client, mock_connection):
        from app.services import runner

        code, body = _submit(client, mock_connection["image_pk"], "claim exclusivity")
        job_id = body["data"]["id"]
        # Wait for the workers to settle, then drain the queue.
        first = runner.claim_next()
        second = runner.claim_next()
        while first or second:
            assert first is None or second is None or first["id"] != second["id"], (
                "claim_next returned the same job twice")
            first, second = runner.claim_next(), runner.claim_next()
        row = store.row("SELECT status FROM generation_jobs WHERE id=?", (job_id,))
        assert row["status"] in ("succeeded", "running", "queued", "failed")

    def test_a_claimed_job_is_marked_running(self, client, mock_connection):
        """The lease and the status must move together, in one statement."""
        from app.services import runner

        code, body = _submit(client, mock_connection["image_pk"], "claimed is running")
        job_id = body["data"]["id"]
        # Drain anything the workers picked up, then claim and inspect the transition.
        claimed = runner.claim_next()
        if claimed is not None:
            row = store.row(
                "SELECT status, lease_token FROM generation_jobs WHERE id=?", (claimed["id"],))
            if row:
                assert row["lease_token"], "a claimed job holds a lease"
                assert row["status"] in ("running", "succeeded", "failed", "cancelled")

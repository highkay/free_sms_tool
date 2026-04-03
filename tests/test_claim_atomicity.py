from __future__ import annotations

import threading

from app.config import Settings
from app.db.bootstrap import initialize_database
from app.db.core import Database
from app.db.repository import Repository


def _seed_single_candidate(database: Database) -> None:
    with database.connection() as conn:
        conn.execute(
            """
            INSERT INTO numbers (
                id, e164, country_name, national_number, status, activity_score,
                first_seen_at, last_seen_at, last_message_at, last_message_age_min, freshness_bucket
            ) VALUES (
                1, '+15550001111', 'United States', '15550001111', 'active', 100.0,
                datetime('now'), datetime('now'), datetime('now'), 5, 'hot'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO number_sources (
                id, number_id, provider_id, provider_number_key, detail_url, discovery_url,
                provider_country_label, source_status, restricted, restricted_reason,
                last_seen_at, last_checked_at, last_success_at, last_real_message_at,
                last_real_message_age_min, raw_snapshot_json
            ) VALUES (
                1, 1, 'receive_smss', '15550001111', 'https://receive-smss.com/sms/15550001111/',
                'https://receive-smss.com/', 'United States', 'open', 0, NULL,
                datetime('now'), datetime('now'), datetime('now'), datetime('now'), 5, '{}'
            )
            """
        )


def test_create_claim_is_atomic_for_single_remaining_number(tmp_path):
    settings = Settings(database_path=tmp_path / "claims.db")
    database = Database(settings.database_path)
    initialize_database(database, settings)
    _seed_single_candidate(database)
    repository = Repository(database=database, settings=settings)

    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[str] = []

    def worker(label: str) -> None:
        barrier.wait(timeout=5)
        try:
            claim = repository.create_claim(app_slug="openai", app_name="OpenAI", purpose=label)
            results.append(claim.claim_token)
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(label,)) for label in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0] == "no available number matched the requested filters"

    with database.connection() as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE status = 'watching'"
        ).fetchone()[0]
        number_count = conn.execute("SELECT COUNT(DISTINCT number_id) FROM claims").fetchone()[0]
    assert active_count == 1
    assert number_count == 1

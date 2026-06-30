from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from eidolon_data import DataStore, load_settings
from eidolon_data.schema.models import (
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    OwnerRow,
    TurnRow,
)


OWNER_ID = "test"
COMPANION_ID = "test-xiaoyi"


async def main() -> None:
    store = DataStore.open(load_settings())
    await store.init_schema()
    try:
        now = datetime.now(UTC)
        async with store.session_factory() as session:
            await _merge(
                session,
                OwnerRow(
                    owner_id=OWNER_ID,
                    display_name="Test Owner",
                    kind="person",
                    status="active",
                    profile_json={"purpose": "mission_control_density_check"},
                    settings_json={"privacy_mode": "safe_summary"},
                    created_at=now - timedelta(days=3),
                    updated_at=now,
                ),
            )
            await _merge(
                session,
                CompanionRow(
                    companion_id=COMPANION_ID,
                    owner_id=OWNER_ID,
                    display_name="小忆 Test",
                    kind="companion",
                    status="active",
                    default_memory_realm_id="test-memory-primary",
                    profile_json={"tone": "calm", "role": "personal sovereign agent"},
                    runtime_config_json={"safe_display": True, "mission_control": True},
                    metadata_json={"seed": "mission_control_test_owner"},
                    created_at=now - timedelta(days=3),
                    updated_at=now,
                ),
            )

            for idx, realm in enumerate(_realms()):
                await _merge(
                    session,
                    MemoryRealmRow(
                        realm_id=realm["realm_id"],
                        owner_id=OWNER_ID,
                        companion_id=COMPANION_ID,
                        engine="mempalace",
                        engine_config_json=realm["engine_config_json"],
                        policy_json=realm["policy_json"],
                        status=realm["status"],
                        created_at=now - timedelta(days=idx + 1),
                        updated_at=now - timedelta(minutes=idx * 11),
                    ),
                )

            for idx, device in enumerate(_devices(now)):
                await _merge(session, DeviceRow(**device, created_at=now - timedelta(days=2, minutes=idx), updated_at=now))

            conversations = _conversations(now)
            for conv in conversations:
                await _merge(session, ConversationRow(**conv["conversation"]))
                for turn in conv["turns"]:
                    await _merge(session, TurnRow(**turn["turn"]))
                    for message in turn["messages"]:
                        await _merge(session, MessageRow(**message))

            for job in _jobs(now):
                await _merge(session, JobRow(**job))

            for event in _events(now):
                await _merge(session, EventRow(**event))

            await session.commit()

        snapshot_hint = {
            "owner_id": OWNER_ID,
            "devices": len(_devices(now)),
            "memory_realms": len(_realms()),
            "jobs": len(_jobs(now)),
            "events": len(_events(now)),
        }
        print(f"seeded mission control test owner: {snapshot_hint}")
    finally:
        await store.close()


async def _merge(session, row) -> None:
    existing = await session.get(type(row), _primary_key(row))
    if existing is None:
        session.add(row)
        return
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if value is None and not column.nullable and column.default is not None:
            continue
        setattr(existing, column.name, value)


def _primary_key(row):
    keys = [column.name for column in row.__table__.primary_key.columns]
    if len(keys) == 1:
        return getattr(row, keys[0])
    return tuple(getattr(row, key) for key in keys)


def _realms() -> list[dict]:
    return [
        {
            "realm_id": "test-memory-primary",
            "status": "active",
            "engine_config_json": {"space": "default.test.primary", "tier": "hot"},
            "policy_json": {"write_policy": "consent_aware", "display": "summary_only"},
        },
        {
            "realm_id": "test-memory-projects",
            "status": "active",
            "engine_config_json": {"space": "default.test.projects", "tier": "warm"},
            "policy_json": {"write_policy": "task_context", "display": "summary_only"},
        },
        {
            "realm_id": "test-memory-sensitive",
            "status": "restricted",
            "engine_config_json": {"space": "default.test.sensitive", "tier": "sealed"},
            "policy_json": {"write_policy": "explicit_consent", "display": "count_only"},
        },
    ]


def _devices(now: datetime) -> list[dict]:
    return [
        {
            "device_id": "test-2-06-pocket",
            "owner_id": OWNER_ID,
            "name": "2.06 Pocket PTT",
            "kind": "esp32_s3_touch_amoled_2.06",
            "status": "online",
            "approved_at": now - timedelta(days=2),
            "approved_by": "mission-control-seed",
            "bound_companion_id": COMPANION_ID,
            "interaction_mode": "ptt_voice",
            "auth_type": "device_secret",
            "secret_ref": "seed://test-2-06",
            "capabilities_json": {"voice": True, "ptt": True, "display": True, "touch": True},
            "network_json": {"room_name": "test-pocket-control", "transport": "livekit"},
            "access_policy_json": {"camera": "none", "voice": "allowed", "summary_only": True},
            "metadata_json": {"source": "mission_control_seed"},
            "last_seen_at": now - timedelta(seconds=18),
            "revoked_at": None,
        },
        {
            "device_id": "test-box-3-desk",
            "owner_id": OWNER_ID,
            "name": "ESP-BOX-3 Desk",
            "kind": "esp_box_3",
            "status": "online",
            "approved_at": now - timedelta(days=2),
            "approved_by": "mission-control-seed",
            "bound_companion_id": COMPANION_ID,
            "interaction_mode": "room_voice",
            "auth_type": "device_secret",
            "secret_ref": "seed://test-box-desk",
            "capabilities_json": {"voice": True, "speaker": True, "display": True, "sensor": True},
            "network_json": {"room_name": "test-desk-control", "transport": "livekit"},
            "access_policy_json": {"voice": "allowed", "device_control": "approved"},
            "metadata_json": {"source": "mission_control_seed"},
            "last_seen_at": now - timedelta(seconds=42),
            "revoked_at": None,
        },
        {
            "device_id": "test-box-3-kitchen",
            "owner_id": OWNER_ID,
            "name": "ESP-BOX-3 Kitchen",
            "kind": "esp_box_3",
            "status": "idle",
            "approved_at": now - timedelta(days=1),
            "approved_by": "mission-control-seed",
            "bound_companion_id": COMPANION_ID,
            "interaction_mode": "ambient_voice",
            "auth_type": "device_secret",
            "secret_ref": "seed://test-box-kitchen",
            "capabilities_json": {"voice": True, "speaker": True, "sensor": True},
            "network_json": {"room_name": "test-kitchen-control", "transport": "livekit"},
            "access_policy_json": {"voice": "push_to_talk_or_wakeword"},
            "metadata_json": {"source": "mission_control_seed"},
            "last_seen_at": now - timedelta(minutes=4),
            "revoked_at": None,
        },
        {
            "device_id": "test-atk-vision",
            "owner_id": OWNER_ID,
            "name": "ATK Vision Node",
            "kind": "atk_camera_screen",
            "status": "online",
            "approved_at": now - timedelta(days=1),
            "approved_by": "mission-control-seed",
            "bound_companion_id": COMPANION_ID,
            "interaction_mode": "vision_authorized",
            "auth_type": "device_secret",
            "secret_ref": "seed://test-atk",
            "capabilities_json": {"camera.snapshot": True, "vision": True, "display": True, "sensor": True},
            "network_json": {"room_name": "test-vision-control", "transport": "hub_command"},
            "access_policy_json": {"camera": "explicit_authorization", "raw_image_retention": False},
            "metadata_json": {"source": "mission_control_seed"},
            "last_seen_at": now - timedelta(seconds=75),
            "revoked_at": None,
        },
        {
            "device_id": "test-web-body",
            "owner_id": OWNER_ID,
            "name": "Web Body",
            "kind": "web_client",
            "status": "active",
            "approved_at": now - timedelta(days=3),
            "approved_by": "mission-control-seed",
            "bound_companion_id": COMPANION_ID,
            "interaction_mode": "text_voice",
            "auth_type": "session",
            "secret_ref": "seed://test-web",
            "capabilities_json": {"voice": True, "display": True, "control": True},
            "network_json": {"room_name": "test-web-session", "transport": "https"},
            "access_policy_json": {"safe_display": True},
            "metadata_json": {"source": "mission_control_seed"},
            "last_seen_at": now - timedelta(seconds=12),
            "revoked_at": None,
        },
    ]


def _conversations(now: datetime) -> list[dict]:
    conversations: list[dict] = []
    for idx, device_id in enumerate(["test-2-06-pocket", "test-box-3-desk", "test-atk-vision"]):
        conv_id = f"test-conv-{idx + 1}"
        turn_id = f"test-turn-{idx + 1}"
        started = now - timedelta(minutes=9 - idx * 3)
        conversations.append(
            {
                "conversation": {
                    "conversation_id": conv_id,
                    "owner_id": OWNER_ID,
                    "companion_id": COMPANION_ID,
                    "runtime_caller_id": f"test-caller-{device_id}",
                    "runtime_session_id": f"test-session-{idx + 1}",
                    "source_device_id": device_id,
                    "title": ["PTT quick capture", "Room recall", "Vision authorization"][idx],
                    "status": "active",
                    "started_at": started,
                    "updated_at": started + timedelta(seconds=75),
                    "ended_at": None,
                    "metadata_json": {"privacy": "summary_only"},
                },
                "turns": [
                    {
                        "turn": {
                            "turn_id": turn_id,
                            "conversation_id": conv_id,
                            "seq": 1,
                            "runtime_caller_id": f"test-caller-{device_id}",
                            "runtime_session_id": f"test-session-{idx + 1}",
                            "source_device_id": device_id,
                            "trigger": "user_utterance",
                            "status": "completed",
                            "started_at": started,
                            "finished_at": started + timedelta(seconds=18 + idx * 3),
                            "trace_json": {"trace_id": f"trace-test-{idx + 1}", "safe_summary": True},
                            "metrics_json": {"latency_ms": 1260 + idx * 430, "memory_hits": idx + 1},
                            "metadata_json": {"runtime_observer": True},
                        },
                        "messages": [
                            {
                                "message_id": f"test-msg-{idx + 1}-u",
                                "turn_id": turn_id,
                                "seq": 1,
                                "role": "user",
                                "content": "[redacted seed user intent]",
                                "content_type": "text/plain",
                                "visibility": "private",
                                "created_at": started,
                                "metadata_json": {"display": "redacted"},
                            },
                            {
                                "message_id": f"test-msg-{idx + 1}-a",
                                "turn_id": turn_id,
                                "seq": 2,
                                "role": "assistant",
                                "content": "[redacted seed assistant response]",
                                "content_type": "text/plain",
                                "visibility": "private",
                                "created_at": started + timedelta(seconds=18 + idx * 3),
                                "metadata_json": {"display": "redacted"},
                            },
                        ],
                    }
                ],
            }
        )
    return conversations


def _jobs(now: datetime) -> list[dict]:
    rows = [
        ("test-job-brief", "mementos", "briefing", "running", 72, "整理跨身体交互摘要"),
        ("test-job-vision", "vision", "scene_summary", "completed", 100, "视觉节点布局摘要已生成"),
        ("test-job-memory", "memory", "steward_write", "queued", 25, "等待记忆 steward 消化"),
        ("test-job-device", "hub", "device_command", "completed", 100, "BOX-3 identify / brightness applied"),
        ("test-job-report", "mementos", "artifact_report", "pending", 8, "准备生成运行态报告"),
        ("test-job-alert", "mission_control", "health_check", "completed", 100, "所有关键链路已采样"),
    ]
    out = []
    for idx, (job_id, provider, kind, status, pct, summary) in enumerate(rows):
        created = now - timedelta(minutes=24 - idx * 3)
        out.append(
            {
                "job_id": job_id,
                "owner_id": OWNER_ID,
                "companion_id": COMPANION_ID,
                "conversation_id": f"test-conv-{(idx % 3) + 1}",
                "turn_id": f"test-turn-{(idx % 3) + 1}",
                "provider": provider,
                "kind": kind,
                "status": status,
                "input_json": {"summary": summary, "privacy": "summary_only"},
                "provider_ref_json": {"seed": True},
                "progress_json": {"summary": summary, "percent": pct},
                "result_json": {"summary": summary} if status == "completed" else {},
                "error_json": {},
                "created_at": created,
                "updated_at": created + timedelta(minutes=idx + 1),
                "completed_at": created + timedelta(minutes=idx + 1) if status == "completed" else None,
            }
        )
    return out


def _events(now: datetime) -> list[dict]:
    specs = [
        ("channel.audio.ptt_pressed", "device", "test-2-06-pocket", {"state": "recording"}),
        ("channel.stt.final", "turn", "test-turn-1", {"transcript": "redacted seed transcript"}),
        ("agent.turn.started", "turn", "test-turn-1", {"trace_id": "trace-test-1"}),
        ("memory.recall.completed", "turn", "test-turn-1", {"hit_count": 3, "realm_id": "test-memory-primary"}),
        ("agent.tool_call.created", "turn", "test-turn-1", {"tool": "save_memory_candidate"}),
        ("memory.write.queued", "turn", "test-turn-1", {"disposition": "queued", "fanout_allowed": True}),
        ("agent.turn.completed", "turn", "test-turn-1", {"latency_ms": 1260}),
        ("device.command.updated", "device", "test-box-3-desk", {"op": "speaker.route_tts", "status": "completed"}),
        ("channel.tts.routed", "device", "test-box-3-desk", {"room": "test-desk-control"}),
        ("memory.fanout.published", "turn", "test-turn-1", {"state": "published"}),
        ("channel.audio.wakeword", "device", "test-box-3-desk", {"state": "listening"}),
        ("agent.turn.started", "turn", "test-turn-2", {"trace_id": "trace-test-2"}),
        ("memory.recall.completed", "turn", "test-turn-2", {"hit_count": 2, "realm_id": "test-memory-projects"}),
        ("agent.tool_result.received", "turn", "test-turn-2", {"tool": "memory_recall", "status": "ok"}),
        ("agent.turn.completed", "turn", "test-turn-2", {"latency_ms": 1690}),
        ("camera.take_photo.requested", "device", "test-atk-vision", {"raw_image_retention": False}),
        ("camera.take_photo.authorized", "device", "test-atk-vision", {"authorized_by": OWNER_ID}),
        ("vision.observation.created", "turn", "test-turn-3", {"summary": "redacted vision layout summary"}),
        ("agent.tool_call.created", "turn", "test-turn-3", {"tool": "delegate_to_coworker"}),
        ("job.created", "job", "test-job-brief", {"provider": "mementos"}),
        ("job.progress.updated", "job", "test-job-brief", {"percent": 72}),
        ("job.completed", "job", "test-job-vision", {"artifact": "vision_summary"}),
        ("job.queued", "job", "test-job-memory", {"queue": "memory_steward"}),
        ("device.command.updated", "device", "test-box-3-desk", {"op": "display.set_brightness", "status": "completed"}),
        ("device.presence.updated", "device", "test-box-3-kitchen", {"status": "idle"}),
        ("permission.audit.recorded", "device", "test-atk-vision", {"capability": "camera.snapshot"}),
        ("memory.steward.extracted", "turn", "test-turn-3", {"candidate_count": 2}),
        ("memory.kg.summary.updated", "turn", "test-turn-3", {"nodes": 4, "edges": 5}),
        ("mission_control.snapshot.sampled", "owner", OWNER_ID, {"source_count": 6}),
        ("agent.proactive.report_back", "job", "test-job-report", {"status": "pending"}),
        ("device.command.updated", "device", "test-2-06-pocket", {"op": "display.status_badge", "status": "completed"}),
        ("channel.room.lifecycle", "device", "test-web-body", {"state": "connected"}),
    ]
    out = []
    for idx, (event_type, subject_type, subject_id, payload) in enumerate(specs):
        out.append(
            {
                "event_id": f"test-mc-event-{idx + 1:02d}",
                "owner_id": OWNER_ID,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "event_type": event_type,
                "actor_type": "system",
                "actor_id": "mission-control-seed",
                "payload_json": payload,
                "created_at": now - timedelta(seconds=(len(specs) - idx) * 18),
            }
        )
    return out


if __name__ == "__main__":
    asyncio.run(main())

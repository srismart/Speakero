import asyncio

import main


def test_disconnect_keeps_session_during_grace():
    async def scenario():
        sess = main.SessionState()
        sess.room = "olds"
        main.SESSIONS["olds"] = sess

        await main.disconnect("olds")

        assert "olds" in main.SESSIONS          # not deleted immediately
        assert sess.cleanup_task is not None    # cleanup scheduled
        sess.cleanup_task.cancel()
        main.SESSIONS.pop("olds", None)
    asyncio.run(scenario())


def test_grace_expiry_cleans_up(monkeypatch):
    monkeypatch.setattr(main, "GRACE_SECONDS", 0.01)

    async def scenario():
        sess = main.SessionState()
        sess.room = "olds"
        main.SESSIONS["olds"] = sess

        await main.disconnect("olds")
        await asyncio.sleep(0.1)

        assert "olds" not in main.SESSIONS
    asyncio.run(scenario())


def test_resume_rekeys_session_to_new_sid():
    async def scenario():
        old = main.SessionState()
        old.room = "olds"
        main.SESSIONS["olds"] = old
        await main.disconnect("olds")

        fresh = main.SessionState()             # what connect() creates for the new sid
        fresh.room = "news"
        main.SESSIONS["news"] = fresh

        resp = await main.resume("news", {"old_sid": "olds"})

        assert resp == {"resumed": True}
        assert main.SESSIONS["news"] is old     # old state re-keyed, fresh discarded
        assert old.room == "news"               # nudges now target the new room
        assert "olds" not in main.SESSIONS
        assert old.cleanup_task is None         # pending cleanup cancelled
        main.SESSIONS.pop("news", None)
    asyncio.run(scenario())


def test_resume_unknown_or_expired_sid_is_refused():
    async def scenario():
        resp = await main.resume("news", {"old_sid": "never-existed"})
        assert resp == {"resumed": False}
    asyncio.run(scenario())

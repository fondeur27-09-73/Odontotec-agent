import os
import logging

logger = logging.getLogger("odontotec.poller")

_last_processed: dict[int, int] = {}
_initialized = False


def _process_sync(conv_id: int, phone: str, content: str):
    from integrations.supabase_client import ensure_patient, save_message, get_messages
    from integrations.chatwoot import send_message
    from agent.claude import run_agent

    max_history = int(os.getenv("MAX_HISTORY", "20"))
    ensure_patient(phone)
    save_message(phone, "user", content)
    history = get_messages(phone, max_history)
    response = run_agent(history, conv_id)
    save_message(phone, "assistant", response)
    send_message(conv_id, response)


def poll_new_messages():
    global _initialized

    from integrations.chatwoot import get_open_conversations, get_conv_messages

    try:
        conversations = get_open_conversations()
    except Exception as e:
        logger.error(f"[poller] fetch conversations error: {e}")
        return

    if not _initialized:
        for conv in conversations:
            try:
                msgs = get_conv_messages(conv["id"])
                incoming = [m for m in msgs if m.get("message_type") == 0]
                if incoming:
                    _last_processed[conv["id"]] = incoming[-1]["id"]
            except Exception:
                pass
        _initialized = True
        logger.info(f"[poller] initialized, tracking {len(_last_processed)} conversations")
        return

    bot_off = os.getenv("BOT_OFF_LABEL", "bot-off")

    for conv in conversations:
        conv_id = conv["id"]

        if bot_off in conv.get("labels", []):
            continue

        try:
            msgs = get_conv_messages(conv_id)
        except Exception as e:
            logger.error(f"[poller] fetch messages error conv={conv_id}: {e}")
            continue

        incoming = [m for m in msgs if m.get("message_type") == 0]
        if not incoming:
            continue

        latest = incoming[-1]
        latest_id = latest["id"]

        if _last_processed.get(conv_id) == latest_id:
            continue

        _last_processed[conv_id] = latest_id

        phone = conv.get("meta", {}).get("sender", {}).get("phone_number")
        content = latest.get("content", "")

        if not content:
            for att in latest.get("attachments", []):
                if att.get("file_type") in ("audio", "audio_file"):
                    try:
                        from utils.audio import transcribe_audio
                        content = transcribe_audio(att.get("data_url", ""))
                    except Exception as e:
                        logger.error(f"[poller] audio error conv={conv_id}: {e}")
                    break

        if not phone or not content:
            continue

        logger.info(f"[poller] processing conv={conv_id} phone={phone}")
        try:
            _process_sync(conv_id, phone, content)
        except Exception as e:
            logger.error(f"[poller] process error conv={conv_id}: {e}", exc_info=True)

import os

def send_nightly_reminders():
    pass

def start_scheduler():
    import pytz
    from apscheduler.schedulers.background import BackgroundScheduler
    tz = pytz.timezone(os.getenv("TIMEZONE", "America/Santo_Domingo"))
    hour = int(os.getenv("REMINDER_HOUR", "20"))
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(send_nightly_reminders, "cron", hour=hour, minute=0)
    scheduler.start()
    return scheduler

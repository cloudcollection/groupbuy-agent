"""Local, foreground scheduler. No system service or account setup is installed."""
from datetime import timezone, timedelta
from .common import GateError

BEIJING = timezone(timedelta(hours=8))


def due_slot(config, now):
    if not config['schedule']['enabled']:
        return None
    local = now.astimezone(BEIJING)
    minute = local.strftime('%H:%M')
    if minute in config['schedule']['times']:
        return local.strftime('%Y-%m-%dT') + minute + '+08:00'
    return None


def tick(runner, now, driver=None):
    if runner.c['mode'] == 'live' and not runner.c['schedule'].get('live_confirmed', False):
        raise GateError('live_schedule_not_validated')
    slot = due_slot(runner.c, now)
    return runner.run(driver=driver, schedule_slot=slot) if slot else {'status': 'NOT_DUE'}


def serve(runner, driver=None):
    import time
    from datetime import datetime
    if not runner.c['schedule']['enabled']:
        raise GateError('schedule_disabled')
    while True:
        tick(runner, datetime.now(timezone.utc), driver)
        time.sleep(20)
